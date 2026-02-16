"""Tests for Phase 4 coordination components.

Tests:
- Lock IDs computation
- Lock registry (acquisition, release, expiry)
- Deadlock detection
- Approval tokens
- Coordination pipeline
"""

import pytest
import uuid
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from coordination.lock_ids import compute_lock_id, compute_lock_set_id, validate_lock_id
from coordination.lock_registry import LockRegistry, LockNotOwnedError, LockOrderViolationError
from coordination.deadlock_graph import DeadlockGraph, TaskNode
from coordination.approval_tokens import (
    ApprovalToken,
    ApprovalTokenSigner,
    ApprovalTokenVerifier,
    compute_payload_hash,
    verify_payload_match
)
from coordination.approval_registry import (
    ApprovalRegistry,
    TokenAlreadyUsedError,
    TokenExpiredError
)


class TestLockIDs:
    """Tests for lock ID computation."""

    def test_compute_lock_id_deterministic(self):
        """Lock IDs are deterministic for same inputs."""
        lock_id_1 = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")
        lock_id_2 = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")
        assert lock_id_1 == lock_id_2

    def test_compute_lock_id_different_resources(self):
        """Different resources produce different lock IDs."""
        lock_id_1 = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")
        lock_id_2 = compute_lock_id("filesystem_path", "/tmp/other.txt", "global")
        assert lock_id_1 != lock_id_2

    def test_compute_lock_id_different_scopes(self):
        """Different scopes produce different lock IDs."""
        lock_id_1 = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")
        lock_id_2 = compute_lock_id("filesystem_path", "/tmp/test.txt", "local")
        assert lock_id_1 != lock_id_2

    def test_compute_lock_set_id(self):
        """Lock set IDs are computed from sorted lock IDs."""
        lock_ids = [
            compute_lock_id("filesystem_path", "/tmp/a.txt", "global"),
            compute_lock_id("filesystem_path", "/tmp/b.txt", "global"),
        ]
        lock_set_id_1 = compute_lock_set_id(sorted(lock_ids))
        lock_set_id_2 = compute_lock_set_id(sorted(lock_ids))
        assert lock_set_id_1 == lock_set_id_2

    def test_validate_lock_id(self):
        """Valid lock IDs pass validation."""
        lock_id = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")
        assert validate_lock_id(lock_id) is True


class TestLockRegistry:
    """Tests for lock registry."""

    def test_acquire_single_lock(self):
        """Can acquire single lock."""
        registry = LockRegistry(lock_ttl_events=1000)
        registry.update_event_seq(100)

        lock_id = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")

        acquired, unavailable = registry.acquire_lock_set(
            lock_ids=[lock_id],
            task_id="task1",
            attempt=0,
            enqueue_seq=1
        )

        assert acquired is True
        assert unavailable is None

        # Verify lock is held
        lock = registry.locks[lock_id]
        assert lock.is_held()
        assert lock.owner_task_id == "task1"
        assert lock.owner_attempt == 0

    def test_acquire_multiple_locks_all_or_nothing(self):
        """All-or-nothing lock acquisition."""
        registry = LockRegistry(lock_ttl_events=1000)
        registry.update_event_seq(100)

        lock_id_1 = compute_lock_id("filesystem_path", "/tmp/a.txt", "global")
        lock_id_2 = compute_lock_id("filesystem_path", "/tmp/b.txt", "global")

        # Acquire first set
        acquired, _ = registry.acquire_lock_set(
            lock_ids=sorted([lock_id_1, lock_id_2]),
            task_id="task1",
            attempt=0,
            enqueue_seq=1
        )

        assert acquired is True

        # Both locks should be held
        assert registry.locks[lock_id_1].is_held()
        assert registry.locks[lock_id_2].is_held()

    def test_lock_acquisition_blocked(self):
        """Lock acquisition blocked if lock already held."""
        registry = LockRegistry(lock_ttl_events=1000)
        registry.update_event_seq(100)

        lock_id = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")

        # Task 1 acquires lock
        acquired, _ = registry.acquire_lock_set(
            lock_ids=[lock_id],
            task_id="task1",
            attempt=0,
            enqueue_seq=1
        )
        assert acquired is True

        # Task 2 tries to acquire - should be blocked
        acquired, unavailable = registry.acquire_lock_set(
            lock_ids=[lock_id],
            task_id="task2",
            attempt=0,
            enqueue_seq=2
        )
        assert acquired is False
        assert unavailable == lock_id

    def test_lock_release(self):
        """Can release locks."""
        registry = LockRegistry(lock_ttl_events=1000)
        registry.update_event_seq(100)

        lock_id = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")

        # Acquire
        registry.acquire_lock_set(
            lock_ids=[lock_id],
            task_id="task1",
            attempt=0,
            enqueue_seq=1
        )

        # Release
        registry.release_lock_set(
            lock_ids=[lock_id],
            task_id="task1",
            attempt=0
        )

        # Lock should no longer be held
        lock = registry.locks[lock_id]
        assert not lock.is_held()

    def test_lock_order_violation(self):
        """Lock acquisition requires sorted lock IDs."""
        registry = LockRegistry(lock_ttl_events=1000)

        lock_id_1 = compute_lock_id("filesystem_path", "/tmp/a.txt", "global")
        lock_id_2 = compute_lock_id("filesystem_path", "/tmp/b.txt", "global")

        # Determine correct order
        sorted_ids = sorted([lock_id_1, lock_id_2])
        unsorted_ids = [sorted_ids[1], sorted_ids[0]]  # Reverse order

        # Unsorted lock IDs should raise error
        with pytest.raises(LockOrderViolationError):
            registry.acquire_lock_set(
                lock_ids=unsorted_ids,  # Wrong order
                task_id="task1",
                attempt=0,
                enqueue_seq=1
            )

    def test_lock_expiry(self):
        """Locks expire based on event_seq."""
        registry = LockRegistry(lock_ttl_events=100)
        registry.update_event_seq(100)

        lock_id = compute_lock_id("filesystem_path", "/tmp/test.txt", "global")

        # Acquire at event_seq 100, expires at 200
        registry.acquire_lock_set(
            lock_ids=[lock_id],
            task_id="task1",
            attempt=0,
            enqueue_seq=1
        )

        lock = registry.locks[lock_id]
        assert not lock.is_expired(150)  # Not expired yet
        assert lock.is_expired(200)  # Expired at expiry time
        assert lock.is_expired(250)  # Expired after


class TestDeadlockGraph:
    """Tests for deadlock detection graph."""

    def test_add_wait_edge(self):
        """Can add wait edges."""
        graph = DeadlockGraph()

        graph.add_wait_edge(
            waiter_task_id="task1",
            waiter_attempt=0,
            waiter_enqueue_seq=1,
            holder_task_id="task2",
            holder_attempt=0,
            holder_enqueue_seq=2,
            blocked_on_lock="lock1"
        )

        assert len(graph.nodes) == 2
        assert len(graph.edges) > 0

    def test_detect_cycle(self):
        """Can detect deadlock cycles."""
        graph = DeadlockGraph()

        # Create cycle: task1 → task2 → task1
        graph.add_wait_edge(
            waiter_task_id="task1",
            waiter_attempt=0,
            waiter_enqueue_seq=1,
            holder_task_id="task2",
            holder_attempt=0,
            holder_enqueue_seq=2,
            blocked_on_lock="lock1"
        )

        graph.add_wait_edge(
            waiter_task_id="task2",
            waiter_attempt=0,
            waiter_enqueue_seq=2,
            holder_task_id="task1",
            holder_attempt=0,
            holder_enqueue_seq=1,
            blocked_on_lock="lock2"
        )

        cycle = graph.detect_cycle()
        assert cycle is not None
        assert len(cycle) >= 2

    def test_select_victim(self):
        """Victim selection is deterministic."""
        graph = DeadlockGraph()

        # Create cycle with different enqueue_seqs
        node1 = TaskNode("task1", 0, 1)  # Earlier
        node2 = TaskNode("task2", 0, 2)  # Later - should be victim

        cycle = [node1, node2]
        victim = graph.select_victim(cycle)

        # Higher enqueue_seq should be selected
        assert victim.enqueue_seq == 2


class TestApprovalTokens:
    """Tests for approval tokens."""

    @pytest.fixture
    def keypair(self):
        """Generate Ed25519 keypair for testing."""
        private_key = Ed25519PrivateKey.generate()
        return private_key, private_key.public_key()

    def test_sign_and_verify_token(self, keypair):
        """Can sign and verify approval tokens."""
        private_key, public_key = keypair

        signer = ApprovalTokenSigner(private_key)
        verifier = ApprovalTokenVerifier(public_key)

        payload = {"action": "test", "data": "value"}
        payload_hash = compute_payload_hash(payload)

        token = ApprovalToken(
            approval_id=str(uuid.uuid4()),
            action="filesystem.write_file",
            payload_hash=payload_hash,
            approver_principal="alice",
            issued_event_seq=0,
            expires_event_seq=1000,
            signature=""
        )

        # Sign token
        signed_token = signer.sign_token(token)
        assert signed_token.signature != ""

        # Verify token
        assert verifier.verify_token(signed_token) is True

    def test_verify_payload_match(self, keypair):
        """Can verify payload matches token hash."""
        payload = {"action": "test", "data": "value"}
        payload_hash = compute_payload_hash(payload)

        token = ApprovalToken(
            approval_id=str(uuid.uuid4()),
            action="filesystem.write_file",
            payload_hash=payload_hash,
            approver_principal="alice",
            issued_event_seq=0,
            expires_event_seq=1000,
            signature=""
        )

        # Same payload should match
        assert verify_payload_match(token, payload) is True

        # Different payload should not match
        different_payload = {"action": "test", "data": "different"}
        assert verify_payload_match(token, different_payload) is False


class TestApprovalRegistry:
    """Tests for approval registry."""

    def test_register_token(self):
        """Can register approval tokens."""
        registry = ApprovalRegistry()
        registry.update_event_seq(0)

        token = ApprovalToken(
            approval_id=str(uuid.uuid4()),
            action="filesystem.write_file",
            payload_hash="abcd1234",
            approver_principal="alice",
            issued_event_seq=0,
            expires_event_seq=1000,
            signature="sig"
        )

        registry.register_token(token)

        assert registry.is_token_available(token.approval_id)

    def test_consume_token_single_use(self):
        """Tokens are single-use only."""
        registry = ApprovalRegistry()
        registry.update_event_seq(0)

        token = ApprovalToken(
            approval_id=str(uuid.uuid4()),
            action="filesystem.write_file",
            payload_hash="abcd1234",
            approver_principal="alice",
            issued_event_seq=0,
            expires_event_seq=1000,
            signature="sig"
        )

        registry.register_token(token)

        # First use succeeds
        consumed = registry.consume_token(token.approval_id, "task1")
        assert consumed is not None

        # Second use fails
        with pytest.raises(TokenAlreadyUsedError):
            registry.consume_token(token.approval_id, "task2")

    def test_token_expiry(self):
        """Expired tokens cannot be consumed."""
        registry = ApprovalRegistry()
        registry.update_event_seq(0)

        token = ApprovalToken(
            approval_id=str(uuid.uuid4()),
            action="filesystem.write_file",
            payload_hash="abcd1234",
            approver_principal="alice",
            issued_event_seq=0,
            expires_event_seq=100,
            signature="sig"
        )

        registry.register_token(token)

        # Advance time past expiry
        registry.update_event_seq(150)

        # Token should be expired
        with pytest.raises(TokenExpiredError):
            registry.consume_token(token.approval_id, "task1")
