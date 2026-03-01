import React, { useState, useRef, useCallback } from 'react';
import { View, FlatList, StyleSheet, KeyboardAvoidingView, Platform } from 'react-native';
import { MessageBubble } from '../components/MessageBubble';
import { ChatInput } from '../components/ChatInput';
import { ModelSelector } from '../components/ModelSelector';
import { streamMessage } from '../services/api';
import { colors } from '../theme/colors';
import type { StreamEvent } from '../types';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  isStreaming?: boolean;
}

export function ChatScreen() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [selectedModel, setSelectedModel] = useState('claude-sonnet-4-5-20250514');
  const [isStreaming, setIsStreaming] = useState(false);
  const flatListRef = useRef<FlatList>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const handleSend = useCallback((text: string) => {
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: text,
    };

    const assistantMsg: ChatMessage = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      content: '',
      isStreaming: true,
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);

    const controller = streamMessage(
      { message: text, conversationId, model: selectedModel },
      (event: StreamEvent) => {
        switch (event.type) {
          case 'start':
            if (event.conversationId) {
              setConversationId(event.conversationId);
            }
            break;
          case 'text':
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last.role === 'assistant') {
                updated[updated.length - 1] = {
                  ...last,
                  content: last.content + (event.text || ''),
                };
              }
              return updated;
            });
            break;
          case 'done':
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last.role === 'assistant') {
                updated[updated.length - 1] = {
                  ...last,
                  isStreaming: false,
                  model: event.model,
                };
              }
              return updated;
            });
            setIsStreaming(false);
            break;
          case 'error':
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last.role === 'assistant') {
                updated[updated.length - 1] = {
                  ...last,
                  content: `Error: ${event.error}`,
                  isStreaming: false,
                };
              }
              return updated;
            });
            setIsStreaming(false);
            break;
        }
      }
    );

    controllerRef.current = controller;
  }, [conversationId, selectedModel]);

  const renderMessage = ({ item }: { item: ChatMessage }) => (
    <MessageBubble
      role={item.role}
      content={item.content}
      model={item.model}
      isStreaming={item.isStreaming}
    />
  );

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={90}
    >
      <ModelSelector selectedModel={selectedModel} onSelect={setSelectedModel} />
      <FlatList
        ref={flatListRef}
        data={messages}
        renderItem={renderMessage}
        keyExtractor={(item) => item.id}
        style={styles.messageList}
        contentContainerStyle={styles.messageContent}
        onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: true })}
      />
      <ChatInput onSend={handleSend} disabled={isStreaming} />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  messageList: {
    flex: 1,
  },
  messageContent: {
    paddingVertical: 8,
  },
});
