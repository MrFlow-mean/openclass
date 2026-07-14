"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import {
  BOARD_MODEL_STORAGE_KEY,
  FALLBACK_MODEL_CATALOG,
  REALTIME_MODEL_STORAGE_KEY,
  TEXT_MODEL_STORAGE_KEY,
  findModelOption,
  normalizeCourseStudioModelCatalog,
  optionToSelection,
  persistModelSelection,
  readStoredModelSelection,
  resolveModelSelection,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import type { AIModelCatalog, AIModelOption, AIModelSelection } from "@/types";

export function useModelCatalog() {
  const [modelCatalog, setModelCatalog] = useState<AIModelCatalog>(() =>
    normalizeCourseStudioModelCatalog(FALLBACK_MODEL_CATALOG)
  );
  const [selectedTextModel, setSelectedTextModel] = useState<AIModelSelection>(FALLBACK_MODEL_CATALOG.defaults.text);
  const [selectedBoardModel, setSelectedBoardModel] = useState<AIModelSelection>(FALLBACK_MODEL_CATALOG.defaults.text);
  const [selectedRealtimeModel, setSelectedRealtimeModel] = useState<AIModelSelection>(
    FALLBACK_MODEL_CATALOG.defaults.realtime
  );
  const [openModelMenu, setOpenModelMenu] = useState<"text" | "realtime" | null>(null);

  useEffect(() => {
    async function loadModelCatalog() {
      try {
        const catalog = normalizeCourseStudioModelCatalog(await api.getAIModels());
        setModelCatalog(catalog);
        setSelectedTextModel(
          resolveModelSelection(catalog.text, readStoredModelSelection(TEXT_MODEL_STORAGE_KEY), catalog.defaults.text)
        );
        setSelectedBoardModel(
          resolveModelSelection(catalog.text, readStoredModelSelection(BOARD_MODEL_STORAGE_KEY), catalog.defaults.text)
        );
        setSelectedRealtimeModel(
          resolveModelSelection(
            catalog.realtime,
            readStoredModelSelection(REALTIME_MODEL_STORAGE_KEY),
            catalog.defaults.realtime
          )
        );
      } catch {
        const fallbackCatalog = normalizeCourseStudioModelCatalog(FALLBACK_MODEL_CATALOG);
        setModelCatalog(fallbackCatalog);
        setSelectedTextModel(
          resolveModelSelection(
            fallbackCatalog.text,
            readStoredModelSelection(TEXT_MODEL_STORAGE_KEY),
            fallbackCatalog.defaults.text
          )
        );
        setSelectedBoardModel(
          resolveModelSelection(
            fallbackCatalog.text,
            readStoredModelSelection(BOARD_MODEL_STORAGE_KEY),
            fallbackCatalog.defaults.text
          )
        );
        setSelectedRealtimeModel(
          resolveModelSelection(
            fallbackCatalog.realtime,
            readStoredModelSelection(REALTIME_MODEL_STORAGE_KEY),
            fallbackCatalog.defaults.realtime
          )
        );
      }
    }
    void loadModelCatalog();
  }, []);

  const selectedTextOption = findModelOption(modelCatalog.text, selectedTextModel);
  const selectedBoardOption = findModelOption(modelCatalog.text, selectedBoardModel);
  const selectedRealtimeOption = findModelOption(modelCatalog.realtime, selectedRealtimeModel);
  const selectedRealtimeTransport =
    selectedRealtimeOption?.transport ??
    (selectedRealtimeModel.provider === "openai" ? "openai_webrtc" : "gemini_live_websocket");

  function selectTextModel(selection: AIModelSelection) {
    const option = findModelOption(modelCatalog.text, selection);
    if (!option?.enabled) {
      return;
    }
    const nextSelection = selectionForModelOption(option, selection);
    setSelectedTextModel(nextSelection);
    persistModelSelection(TEXT_MODEL_STORAGE_KEY, nextSelection);
    setOpenModelMenu(null);
  }

  function selectBoardModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    const nextSelection = optionToSelection(option);
    setSelectedBoardModel(nextSelection);
    persistModelSelection(BOARD_MODEL_STORAGE_KEY, nextSelection);
  }

  function selectRealtimeModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    const nextSelection = optionToSelection(option);
    setSelectedRealtimeModel(nextSelection);
    persistModelSelection(REALTIME_MODEL_STORAGE_KEY, nextSelection);
    setOpenModelMenu(null);
  }

  return {
    modelCatalog,
    selectedTextModel,
    selectedBoardModel,
    selectedRealtimeModel,
    selectedTextOption,
    selectedBoardOption,
    selectedRealtimeOption,
    selectedRealtimeTransport,
    openModelMenu,
    setOpenModelMenu,
    selectTextModel,
    selectBoardModel,
    selectRealtimeModel,
  };
}
