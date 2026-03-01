import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  RefreshControl,
  Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import { getConversations, deleteConversation } from '../services/api';
import { colors } from '../theme/colors';
import type { Conversation } from '../types';

export function ConversationsScreen() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await getConversations();
      setConversations(res.conversations);
    } catch (err: any) {
      Alert.alert('Error', err.message);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const handleRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const handleDelete = (id: string, title: string) => {
    Alert.alert('Delete Conversation', `Delete "${title}"?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            await deleteConversation(id);
            setConversations((prev) => prev.filter((c) => c.id !== id));
          } catch (err: any) {
            Alert.alert('Error', err.message);
          }
        },
      },
    ]);
  };

  const renderItem = ({ item }: { item: Conversation }) => (
    <TouchableOpacity style={styles.item}>
      <View style={styles.itemContent}>
        <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
        <Text style={styles.meta}>
          {item.messageCount} messages · {item.model?.split('-').slice(1, 2).join('')}
        </Text>
        <Text style={styles.date}>
          {new Date(item.updatedAt).toLocaleDateString()}
        </Text>
      </View>
      <TouchableOpacity
        style={styles.deleteButton}
        onPress={() => handleDelete(item.id, item.title || 'Untitled')}
      >
        <Ionicons name="trash-outline" size={20} color={colors.error} />
      </TouchableOpacity>
    </TouchableOpacity>
  );

  return (
    <View style={styles.container}>
      <FlatList
        data={conversations}
        renderItem={renderItem}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={colors.primary} />}
        contentContainerStyle={conversations.length === 0 ? styles.empty : undefined}
        ListEmptyComponent={
          <View style={styles.emptyContent}>
            <Ionicons name="chatbubbles-outline" size={48} color={colors.textLight} />
            <Text style={styles.emptyText}>No conversations yet</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  item: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  itemContent: { flex: 1 },
  title: {
    fontSize: 16,
    fontWeight: '600',
    color: colors.text,
  },
  meta: {
    fontSize: 13,
    color: colors.textSecondary,
    marginTop: 2,
  },
  date: {
    fontSize: 12,
    color: colors.textLight,
    marginTop: 2,
  },
  deleteButton: {
    padding: 8,
  },
  empty: { flex: 1, justifyContent: 'center' },
  emptyContent: { alignItems: 'center' },
  emptyText: {
    fontSize: 16,
    color: colors.textLight,
    marginTop: 12,
  },
});
