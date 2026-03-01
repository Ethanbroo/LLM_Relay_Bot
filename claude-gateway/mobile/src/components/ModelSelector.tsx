import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

interface Props {
  selectedModel: string;
  onSelect: (modelId: string) => void;
}

const MODELS = [
  { id: 'claude-haiku-4-5-20251001', label: 'Haiku', tier: 'haiku' as const, desc: 'Fast' },
  { id: 'claude-sonnet-4-5-20250514', label: 'Sonnet', tier: 'sonnet' as const, desc: 'Balanced' },
  { id: 'claude-opus-4-6', label: 'Opus', tier: 'opus' as const, desc: 'Powerful' },
];

export function ModelSelector({ selectedModel, onSelect }: Props) {
  return (
    <View style={styles.container}>
      {MODELS.map((model) => {
        const isSelected = selectedModel === model.id;
        return (
          <TouchableOpacity
            key={model.id}
            style={[
              styles.chip,
              { borderColor: colors[model.tier] },
              isSelected && { backgroundColor: colors[model.tier] },
            ]}
            onPress={() => onSelect(model.id)}
          >
            <Text style={[styles.label, isSelected && styles.labelSelected]}>
              {model.label}
            </Text>
            <Text style={[styles.desc, isSelected && styles.descSelected]}>
              {model.desc}
            </Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  chip: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 2,
    alignItems: 'center',
    minWidth: 90,
  },
  label: {
    fontSize: 14,
    fontWeight: '600',
    color: colors.text,
  },
  labelSelected: {
    color: colors.textOnPrimary,
  },
  desc: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 1,
  },
  descSelected: {
    color: 'rgba(255,255,255,0.85)',
  },
});
