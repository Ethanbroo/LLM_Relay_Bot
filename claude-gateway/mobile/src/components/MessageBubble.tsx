import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';

interface Props {
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  isStreaming?: boolean;
}

export function MessageBubble({ role, content, model, isStreaming }: Props) {
  const isUser = role === 'user';

  return (
    <View style={[styles.container, isUser ? styles.userContainer : styles.assistantContainer]}>
      <View style={[styles.bubble, isUser ? styles.userBubble : styles.assistantBubble]}>
        <Text style={[styles.text, isUser ? styles.userText : styles.assistantText]}>
          {content}
          {isStreaming && <Text style={styles.cursor}>|</Text>}
        </Text>
      </View>
      {model && !isUser && (
        <Text style={styles.modelLabel}>{model}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginVertical: 4,
    marginHorizontal: 12,
  },
  userContainer: {
    alignItems: 'flex-end',
  },
  assistantContainer: {
    alignItems: 'flex-start',
  },
  bubble: {
    maxWidth: '85%',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 18,
  },
  userBubble: {
    backgroundColor: colors.userBubble,
    borderBottomRightRadius: 4,
  },
  assistantBubble: {
    backgroundColor: colors.assistantBubble,
    borderBottomLeftRadius: 4,
  },
  text: {
    fontSize: 16,
    lineHeight: 22,
  },
  userText: {
    color: colors.userBubbleText,
  },
  assistantText: {
    color: colors.assistantBubbleText,
  },
  cursor: {
    color: colors.primary,
    fontWeight: 'bold',
  },
  modelLabel: {
    fontSize: 11,
    color: colors.textLight,
    marginTop: 2,
    marginLeft: 8,
  },
});
