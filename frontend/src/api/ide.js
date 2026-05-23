/**
 * ide.js — API aggregator
 *
 * Re-exports everything from domain modules so that existing import paths
 * (`import { api, executeStream } from "../api/ide"`) keep working without
 * changes in consumers.
 */

import { listChats, createChat, renameChat, pinChat, saveChatToMemory, deleteChat, getMessages, addMessage, sendMessage } from "./chats";
import { execute, executeStream, listOllamaModels, getSettings, updateSettings } from "./agent";
import { getProjectSnapshot, getProjectFile, getProjectBrainStatus, getPersonaStatus, getRuntimeStatus, getAgentOsHealth, getAgentOsDashboard, listAgentOsLimits, getPersonaVersion, listPersonaCandidates, rollbackPersona, getDashboardOverview } from "./dashboard";
import { extractUploadedFileText, listLibraryFiles, uploadLibraryFile, deleteLibraryFile, listPatchHistory, previewPatch, applyPatch, rollbackPatch, verifyPatch } from "./library";
import { listTasks, getTaskStats, getTasksOverview, createTask, updateTask, deleteTask, listPipelines, createPipeline, runPipeline, updatePipeline, deletePipeline } from "./tasks";
import { getTelegramConfig, listTelegramUsers, getTelegramLog, getTelegramOverview, startTelegramBot, stopTelegramBot, testTelegramBot, updateTelegramConfig, toggleTelegramUser, listPlugins, reloadPlugins, setPluginEnabled } from "./integrations";
import { getAdvancedProjectInfo, openAdvancedProject, getAdvancedProjectTree, readAdvancedProjectFile, searchAdvancedProject, closeAdvancedProject, runAdvancedMultiAgent, getGitStatus, getGitLog, getGitDiff, createGitCommit, listToolRuns, runPythonCode, analyzeCode, diffFile, writeFile, listSmartMemory, getSmartMemoryStats, addSmartMemory, deleteSmartMemory, searchSmartMemory, getTerminalCwd, executeTerminal } from "./advanced";
import { isLocalApiAssetUrl } from "./apiUtils";

// Named re-exports used directly by consumers
export { executeStream, isLocalApiAssetUrl };

export const api = {
  // Chats
  listChats, createChat, renameChat, pinChat, saveChatToMemory, deleteChat, getMessages, addMessage, sendMessage,
  // Agent / stream
  execute, executeStream, listOllamaModels, getSettings, updateSettings,
  // Dashboard / status
  getProjectSnapshot, getProjectFile, getProjectBrainStatus, getPersonaStatus, getRuntimeStatus,
  getAgentOsHealth, getAgentOsDashboard, listAgentOsLimits, getPersonaVersion, listPersonaCandidates,
  rollbackPersona, getDashboardOverview,
  // Library / patch
  extractUploadedFileText, listLibraryFiles, uploadLibraryFile, deleteLibraryFile,
  listPatchHistory, previewPatch, applyPatch, rollbackPatch, verifyPatch,
  // Tasks / pipelines
  listTasks, getTaskStats, getTasksOverview, createTask, updateTask, deleteTask,
  listPipelines, createPipeline, runPipeline, updatePipeline, deletePipeline,
  // Telegram / plugins
  getTelegramConfig, listTelegramUsers, getTelegramLog, getTelegramOverview,
  startTelegramBot, stopTelegramBot, testTelegramBot, updateTelegramConfig, toggleTelegramUser,
  listPlugins, reloadPlugins, setPluginEnabled,
  // Advanced project / git / tools / memory / terminal
  getAdvancedProjectInfo, openAdvancedProject, getAdvancedProjectTree, readAdvancedProjectFile,
  searchAdvancedProject, closeAdvancedProject, runAdvancedMultiAgent,
  getGitStatus, getGitLog, getGitDiff, createGitCommit,
  listToolRuns, runPythonCode, analyzeCode, diffFile, writeFile,
  listSmartMemory, getSmartMemoryStats, addSmartMemory, deleteSmartMemory, searchSmartMemory,
  getTerminalCwd, executeTerminal,
  // Utils
  isLocalApiAssetUrl,
};

export default api;
