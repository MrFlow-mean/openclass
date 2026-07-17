"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { generateGeometryScene } from "@/lib/geometry-api";
import type { AIModelSelection, SelectionRef } from "@/types";
import type { GeometryScene } from "@/types/geometry";

type UseGeometryWorkspaceOptions = {
  lessonId: string;
  incomingSelection: SelectionRef | null;
  textModel: AIModelSelection | null;
  onClearSelection: () => void;
};

type GeometryWorkspaceState = {
  referenceKey: string;
  instructions: string;
  scene: GeometryScene | null;
  error: string;
  isGenerating: boolean;
};

function emptyWorkspaceState(referenceKey = ""): GeometryWorkspaceState {
  return {
    referenceKey,
    instructions: "",
    scene: null,
    error: "",
    isGenerating: false,
  };
}

export function useGeometryWorkspace({
  lessonId,
  incomingSelection,
  textModel,
  onClearSelection,
}: UseGeometryWorkspaceOptions) {
  const requestRef = useRef<AbortController | null>(null);
  const [workspaceState, setWorkspaceState] = useState<GeometryWorkspaceState>(() => emptyWorkspaceState());
  const selection =
    incomingSelection?.kind === "board" &&
    (!incomingSelection.lesson_id || incomingSelection.lesson_id === lessonId)
      ? incomingSelection
      : null;
  const referenceKey = selection
    ? [lessonId, selection.document_id ?? "", selection.excerpt].join("\u001f")
    : "";
  const currentState =
    workspaceState.referenceKey === referenceKey ? workspaceState : emptyWorkspaceState(referenceKey);

  const setInstructions = useCallback(
    (value: string) => {
      setWorkspaceState((current) => ({
        ...(current.referenceKey === referenceKey ? current : emptyWorkspaceState(referenceKey)),
        instructions: value,
      }));
    },
    [referenceKey]
  );

  useEffect(
    () => () => {
      requestRef.current?.abort();
    },
    []
  );

  const generate = useCallback(async () => {
    if (!selection || currentState.isGenerating) {
      return;
    }
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setWorkspaceState({
      ...currentState,
      referenceKey,
      isGenerating: true,
      error: "",
    });
    try {
      const nextScene = await generateGeometryScene(
        lessonId,
        {
          selection,
          instructions: currentState.instructions.trim(),
          text_model: textModel,
        },
        { signal: controller.signal }
      );
      if (!controller.signal.aborted) {
        setWorkspaceState((current) => ({
          ...(current.referenceKey === referenceKey ? current : emptyWorkspaceState(referenceKey)),
          scene: nextScene,
          error: "",
        }));
      }
    } catch (generationError) {
      if (!controller.signal.aborted) {
        setWorkspaceState((current) => ({
          ...(current.referenceKey === referenceKey ? current : emptyWorkspaceState(referenceKey)),
          error: generationError instanceof Error ? generationError.message : "图形生成失败，请稍后重试",
        }));
      }
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setWorkspaceState((current) =>
          current.referenceKey === referenceKey ? { ...current, isGenerating: false } : current
        );
      }
    }
  }, [currentState, lessonId, referenceKey, selection, textModel]);

  const clear = useCallback(() => {
    requestRef.current?.abort();
    requestRef.current = null;
    setWorkspaceState(emptyWorkspaceState());
    onClearSelection();
  }, [onClearSelection]);

  return {
    selection,
    instructions: currentState.instructions,
    setInstructions,
    scene: currentState.scene,
    error: currentState.error,
    isGenerating: currentState.isGenerating,
    generate,
    clear,
  };
}
