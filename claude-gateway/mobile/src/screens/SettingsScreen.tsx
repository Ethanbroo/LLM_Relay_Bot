import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TextInput,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import { configure, getUsage, healthCheck } from '../services/api';
import { colors } from '../theme/colors';
import type { UsageStats } from '../types';

export function SettingsScreen() {
  const [serverUrl, setServerUrl] = useState('http://localhost:3000');
  const [token, setToken] = useState('');
  const [serverStatus, setServerStatus] = useState<'unknown' | 'ok' | 'error'>('unknown');
  const [usage, setUsage] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(false);

  const handleConnect = async () => {
    if (!serverUrl.trim() || !token.trim()) {
      Alert.alert('Error', 'Server URL and access token are required');
      return;
    }

    setLoading(true);
    configure(serverUrl.trim(), token.trim());
    try {
      await healthCheck();
      setServerStatus('ok');
      Alert.alert('Connected', 'Successfully connected to Claude Gateway');
    } catch {
      setServerStatus('error');
      Alert.alert('Connection Failed', 'Could not reach the server. Check your URL.');
    } finally {
      setLoading(false);
    }
  };

  useFocusEffect(
    useCallback(() => {
      if (serverStatus === 'ok') {
        getUsage().then(setUsage).catch(() => {});
      }
    }, [serverStatus])
  );

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.sectionTitle}>Server Connection</Text>
      <View style={styles.card}>
        <Text style={styles.label}>Server URL</Text>
        <TextInput
          style={styles.input}
          value={serverUrl}
          onChangeText={setServerUrl}
          placeholder="https://your-gateway.railway.app"
          placeholderTextColor={colors.textLight}
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Text style={styles.label}>Access Token</Text>
        <TextInput
          style={styles.input}
          value={token}
          onChangeText={setToken}
          placeholder="Your bearer token"
          placeholderTextColor={colors.textLight}
          secureTextEntry
          autoCapitalize="none"
        />
        <TouchableOpacity style={styles.connectButton} onPress={handleConnect} disabled={loading}>
          {loading ? (
            <ActivityIndicator color={colors.textOnPrimary} />
          ) : (
            <Text style={styles.connectText}>Connect</Text>
          )}
        </TouchableOpacity>
        <View style={styles.statusRow}>
          <Ionicons
            name={serverStatus === 'ok' ? 'checkmark-circle' : serverStatus === 'error' ? 'close-circle' : 'ellipse-outline'}
            size={18}
            color={serverStatus === 'ok' ? colors.success : serverStatus === 'error' ? colors.error : colors.textLight}
          />
          <Text style={styles.statusText}>
            {serverStatus === 'ok' ? 'Connected' : serverStatus === 'error' ? 'Connection failed' : 'Not connected'}
          </Text>
        </View>
      </View>

      {usage && (
        <>
          <Text style={styles.sectionTitle}>Usage Statistics</Text>
          <View style={styles.card}>
            <View style={styles.statRow}>
              <Text style={styles.statLabel}>Total Requests</Text>
              <Text style={styles.statValue}>{usage.totals.requests.toLocaleString()}</Text>
            </View>
            <View style={styles.statRow}>
              <Text style={styles.statLabel}>Input Tokens</Text>
              <Text style={styles.statValue}>{usage.totals.inputTokens.toLocaleString()}</Text>
            </View>
            <View style={styles.statRow}>
              <Text style={styles.statLabel}>Output Tokens</Text>
              <Text style={styles.statValue}>{usage.totals.outputTokens.toLocaleString()}</Text>
            </View>
            <View style={styles.statRow}>
              <Text style={styles.statLabel}>Conversations</Text>
              <Text style={styles.statValue}>{usage.conversations.toLocaleString()}</Text>
            </View>
          </View>

          {usage.byModel.length > 0 && (
            <>
              <Text style={styles.sectionTitle}>Usage by Model</Text>
              <View style={styles.card}>
                {usage.byModel.map((m) => (
                  <View key={m.model} style={styles.statRow}>
                    <Text style={styles.statLabel}>{m.model}</Text>
                    <Text style={styles.statValue}>{m.requests} req</Text>
                  </View>
                ))}
              </View>
            </>
          )}
        </>
      )}

      <Text style={styles.footer}>Claude Gateway v1.0.0</Text>
      <Text style={styles.footerSub}>Independent tool — not affiliated with Anthropic</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, paddingBottom: 40 },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.textSecondary,
    marginTop: 20,
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: 16,
    borderWidth: 1,
    borderColor: colors.borderLight,
  },
  label: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.text,
    marginBottom: 4,
    marginTop: 8,
  },
  input: {
    backgroundColor: colors.background,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 15,
    color: colors.text,
    borderWidth: 1,
    borderColor: colors.border,
  },
  connectButton: {
    backgroundColor: colors.primary,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
    marginTop: 14,
  },
  connectText: {
    color: colors.textOnPrimary,
    fontSize: 16,
    fontWeight: '700',
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 10,
    gap: 6,
  },
  statusText: {
    fontSize: 14,
    color: colors.textSecondary,
  },
  statRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  statLabel: {
    fontSize: 14,
    color: colors.text,
  },
  statValue: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.primary,
  },
  footer: {
    textAlign: 'center',
    fontSize: 13,
    color: colors.textLight,
    marginTop: 30,
  },
  footerSub: {
    textAlign: 'center',
    fontSize: 11,
    color: colors.textLight,
    marginTop: 4,
  },
});
