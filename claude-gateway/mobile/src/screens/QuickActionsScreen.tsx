import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  TextInput,
  Alert,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { quickAction } from '../services/api';
import { colors } from '../theme/colors';

const ACTIONS = [
  { id: 'draft-email', icon: 'mail-outline' as const, label: 'Draft Email', category: 'cardinal-sales' },
  { id: 'pricing-response', icon: 'pricetag-outline' as const, label: 'Pricing', category: 'cardinal-sales' },
  { id: 'delivery-update', icon: 'car-outline' as const, label: 'Delivery', category: 'cardinal-sales' },
  { id: 'order-confirmation', icon: 'checkmark-circle-outline' as const, label: 'Order', category: 'cardinal-sales' },
  { id: 'meeting-scheduler', icon: 'calendar-outline' as const, label: 'Meeting', category: 'cardinal-sales' },
  { id: 'summarize', icon: 'document-text-outline' as const, label: 'Summarize', category: 'general' },
  { id: 'code-help', icon: 'code-slash-outline' as const, label: 'Code Help', category: 'general' },
  { id: 'project-plan', icon: 'list-outline' as const, label: 'Plan', category: 'general' },
];

export function QuickActionsScreen() {
  const [selectedAction, setSelectedAction] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleExecute = async () => {
    if (!selectedAction || !input.trim()) return;

    setLoading(true);
    setResult(null);
    try {
      const response = await quickAction(selectedAction, input.trim());
      setResult(response.message);
    } catch (err: any) {
      Alert.alert('Error', err.message);
    } finally {
      setLoading(false);
    }
  };

  const cardinalActions = ACTIONS.filter((a) => a.category === 'cardinal-sales');
  const generalActions = ACTIONS.filter((a) => a.category === 'general');

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.sectionTitle}>Cardinal Sales</Text>
      <View style={styles.grid}>
        {cardinalActions.map((action) => (
          <TouchableOpacity
            key={action.id}
            style={[styles.card, selectedAction === action.id && styles.cardSelected]}
            onPress={() => setSelectedAction(action.id)}
          >
            <Ionicons
              name={action.icon}
              size={28}
              color={selectedAction === action.id ? colors.textOnPrimary : colors.primary}
            />
            <Text style={[styles.cardLabel, selectedAction === action.id && styles.cardLabelSelected]}>
              {action.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.sectionTitle}>General</Text>
      <View style={styles.grid}>
        {generalActions.map((action) => (
          <TouchableOpacity
            key={action.id}
            style={[styles.card, selectedAction === action.id && styles.cardSelected]}
            onPress={() => setSelectedAction(action.id)}
          >
            <Ionicons
              name={action.icon}
              size={28}
              color={selectedAction === action.id ? colors.textOnPrimary : colors.primary}
            />
            <Text style={[styles.cardLabel, selectedAction === action.id && styles.cardLabelSelected]}>
              {action.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {selectedAction && (
        <View style={styles.inputSection}>
          <TextInput
            style={styles.textInput}
            value={input}
            onChangeText={setInput}
            placeholder="Describe what you need..."
            placeholderTextColor={colors.textLight}
            multiline
            numberOfLines={4}
          />
          <TouchableOpacity
            style={[styles.executeButton, (!input.trim() || loading) && styles.executeDisabled]}
            onPress={handleExecute}
            disabled={!input.trim() || loading}
          >
            {loading ? (
              <ActivityIndicator color={colors.textOnPrimary} />
            ) : (
              <Text style={styles.executeText}>Run Action</Text>
            )}
          </TouchableOpacity>
        </View>
      )}

      {result && (
        <View style={styles.resultBox}>
          <Text style={styles.resultLabel}>Result</Text>
          <Text style={styles.resultText} selectable>{result}</Text>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16 },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.textSecondary,
    marginTop: 12,
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 8,
  },
  card: {
    width: '30%',
    aspectRatio: 1,
    backgroundColor: colors.surface,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: colors.borderLight,
  },
  cardSelected: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  cardLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.text,
    marginTop: 6,
  },
  cardLabelSelected: {
    color: colors.textOnPrimary,
  },
  inputSection: { marginTop: 16 },
  textInput: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 14,
    fontSize: 16,
    color: colors.text,
    minHeight: 100,
    textAlignVertical: 'top',
    borderWidth: 1,
    borderColor: colors.border,
  },
  executeButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 12,
  },
  executeDisabled: { opacity: 0.5 },
  executeText: {
    color: colors.textOnPrimary,
    fontSize: 16,
    fontWeight: '700',
  },
  resultBox: {
    marginTop: 16,
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: colors.border,
  },
  resultLabel: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.textSecondary,
    marginBottom: 6,
  },
  resultText: {
    fontSize: 15,
    color: colors.text,
    lineHeight: 22,
  },
});
