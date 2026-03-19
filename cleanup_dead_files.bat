@echo off
REM ═══════════════════════════════════════════════════
REM Jarvis Cleanup — удаляет мёртвые файлы
REM Запускай из корня проекта: cleanup_dead_files.bat
REM ═══════════════════════════════════════════════════

echo.
echo   Jarvis Cleanup - удаление неиспользуемых файлов
echo   ================================================
echo.

REM === BACKEND: мёртвые роуты ===
del /q "backend\app\api\routes\phase10.py" 2>nul
del /q "backend\app\api\routes\phase11.py" 2>nul
del /q "backend\app\api\routes\phase12.py" 2>nul
del /q "backend\app\api\routes\health.py" 2>nul
del /q "backend\app\api\routes\agent.py" 2>nul
del /q "backend\app\api\routes\agent_pipeline.py" 2>nul
del /q "backend\app\api\routes\agent_runs.py" 2>nul
del /q "backend\app\api\routes\agent_runtime.py" 2>nul
del /q "backend\app\api\routes\agent_supervisor.py" 2>nul
del /q "backend\app\api\routes\autonomous_dev.py" 2>nul
del /q "backend\app\api\routes\browser_runtime.py" 2>nul
del /q "backend\app\api\routes\dependency_graph.py" 2>nul
del /q "backend\app\api\routes\desktop_bridge.py" 2>nul
del /q "backend\app\api\routes\desktop_lifecycle.py" 2>nul
del /q "backend\app\api\routes\desktop_runtime.py" 2>nul
del /q "backend\app\api\routes\jarvis_autocode.py" 2>nul
del /q "backend\app\api\routes\jarvis_autoloop.py" 2>nul
del /q "backend\app\api\routes\jarvis_run_history.py" 2>nul
del /q "backend\app\api\routes\jarvis_supervisor_auto.py" 2>nul
del /q "backend\app\api\routes\multi_agent.py" 2>nul
del /q "backend\app\api\routes\project_patch.py" 2>nul
del /q "backend\app\api\routes\run_history.py" 2>nul
del /q "backend\app\api\routes\settings.py" 2>nul
del /q "backend\app\api\routes\stage6_agent.py" 2>nul
del /q "backend\app\api\routes\tools.py" 2>nul

REM === BACKEND: мёртвые сервисы ===
del /q "backend\app\services\agent_executor.py" 2>nul
del /q "backend\app\services\agent_roles.py" 2>nul
del /q "backend\app\services\agent_runtime_service.py" 2>nul
del /q "backend\app\services\agent_supervisor_service.py" 2>nul
del /q "backend\app\services\agent_task_planner.py" 2>nul
del /q "backend\app\services\app_lifecycle_service.py" 2>nul
del /q "backend\app\services\autonomous_dev_engine_service.py" 2>nul
del /q "backend\app\services\backend_process_service.py" 2>nul
del /q "backend\app\services\browser_agent.py" 2>nul
del /q "backend\app\services\browser_runtime_service.py" 2>nul
del /q "backend\app\services\chat_memory_sqlite.py" 2>nul
del /q "backend\app\services\code_dependency_graph_service.py" 2>nul
del /q "backend\app\services\coder_agent_service.py" 2>nul
del /q "backend\app\services\desktop_launch_config_service.py" 2>nul
del /q "backend\app\services\desktop_runtime_service.py" 2>nul
del /q "backend\app\services\embedding_service.py" 2>nul
del /q "backend\app\services\event_bus_service.py" 2>nul
del /q "backend\app\services\execution_history_service.py" 2>nul
del /q "backend\app\services\execution_runtime_bridge_service.py" 2>nul
del /q "backend\app\services\execution_runtime_service.py" 2>nul
del /q "backend\app\services\execution_view_service.py" 2>nul
del /q "backend\app\services\faiss_store.py" 2>nul
del /q "backend\app\services\file_tool.py" 2>nul
del /q "backend\app\services\git_service.py" 2>nul
del /q "backend\app\services\intent_router.py" 2>nul
del /q "backend\app\services\knowledge_base.py" 2>nul
del /q "backend\app\services\legacy_agents_service.py" 2>nul
del /q "backend\app\services\library_rag_integration.py" 2>nul
del /q "backend\app\services\memory_rag_service.py" 2>nul
del /q "backend\app\services\memory_weights.py" 2>nul
del /q "backend\app\services\multi_agent_service.py" 2>nul
del /q "backend\app\services\multi_engine_search.py" 2>nul
del /q "backend\app\services\multi_search_service.py" 2>nul
del /q "backend\app\services\ollama_embedding_service.py" 2>nul
del /q "backend\app\services\ollama_runtime_service.py" 2>nul
del /q "backend\app\services\project_brain_loop_service.py" 2>nul
del /q "backend\app\services\project_map_service.py" 2>nul
del /q "backend\app\services\python_executor.py" 2>nul
del /q "backend\app\services\rag_service.py" 2>nul
del /q "backend\app\services\real_faiss_store.py" 2>nul
del /q "backend\app\services\reflection_service.py" 2>nul
del /q "backend\app\services\research_agent_service.py" 2>nul
del /q "backend\app\services\research_pipeline_service.py" 2>nul
del /q "backend\app\services\reviewer_agent_service.py" 2>nul
del /q "backend\app\services\router_service.py" 2>nul
del /q "backend\app\services\run_trace_service.py" 2>nul
del /q "backend\app\services\russian_system_prompt.py" 2>nul
del /q "backend\app\services\safe_patch_engine_service.py" 2>nul
del /q "backend\app\services\search_config.py" 2>nul
del /q "backend\app\services\semantic_search.py" 2>nul
del /q "backend\app\services\settings_service.py" 2>nul
del /q "backend\app\services\supervisor_runtime_bridge_service.py" 2>nul
del /q "backend\app\services\system_state_service.py" 2>nul
del /q "backend\app\services\task_graph.py" 2>nul
del /q "backend\app\services\task_scheduler_service.py" 2>nul
del /q "backend\app\services\tool_learning_service.py" 2>nul
del /q "backend\app\services\tool_registry.py" 2>nul
del /q "backend\app\services\vector_store.py" 2>nul
del /q "backend\app\services\web_multisearch_service.py" 2>nul

REM === FRONTEND: мёртвые компоненты ===
del /q "frontend\src\components\AgentPanel.jsx" 2>nul
del /q "frontend\src\components\AgentsView.jsx" 2>nul
del /q "frontend\src\components\App.jsx" 2>nul
del /q "frontend\src\components\AppHeader.jsx" 2>nul
del /q "frontend\src\components\AutoCodingPanel.jsx" 2>nul
del /q "frontend\src\components\AutoLoopPanel.jsx" 2>nul
del /q "frontend\src\components\AutonomousDevPanel.jsx" 2>nul
del /q "frontend\src\components\BackendControlPanel.jsx" 2>nul
del /q "frontend\src\components\BatchVerifyPanel.jsx" 2>nul
del /q "frontend\src\components\Chat.jsx" 2>nul
del /q "frontend\src\components\ChatInputHotfix.jsx" 2>nul
del /q "frontend\src\components\ChatPageHotfix.jsx" 2>nul
del /q "frontend\src\components\CodeEditor.jsx" 2>nul
del /q "frontend\src\components\CodeWorkspace.jsx" 2>nul
del /q "frontend\src\components\DesktopStatusBar.jsx" 2>nul
del /q "frontend\src\components\DiffViewer.jsx" 2>nul
del /q "frontend\src\components\FileExplorer.jsx" 2>nul
del /q "frontend\src\components\FileExplorerPanel.jsx" 2>nul
del /q "frontend\src\components\FileOpsPanel.jsx" 2>nul
del /q "frontend\src\components\JarvisLayout.jsx" 2>nul
del /q "frontend\src\components\JarvisWorkspaceShell.jsx" 2>nul
del /q "frontend\src\components\LibraryView.jsx" 2>nul
del /q "frontend\src\components\MemoryView.jsx" 2>nul
del /q "frontend\src\components\MultiAgentPanel.jsx" 2>nul
del /q "frontend\src\components\PatchHistoryPanel.jsx" 2>nul
del /q "frontend\src\components\PatchPlanPanel.jsx" 2>nul
del /q "frontend\src\components\Phase10Panel.jsx" 2>nul
del /q "frontend\src\components\Phase11Panel.jsx" 2>nul
del /q "frontend\src\components\Phase12Panel.jsx" 2>nul
del /q "frontend\src\components\Phase19Panel.jsx" 2>nul
del /q "frontend\src\components\Phase20Panel.jsx" 2>nul
del /q "frontend\src\components\Phase21Panel.jsx" 2>nul
del /q "frontend\src\components\Phase21StatusStrip.jsx" 2>nul
del /q "frontend\src\components\ProjectBrainPanel.jsx" 2>nul
del /q "frontend\src\components\ProjectMapPanel.jsx" 2>nul
del /q "frontend\src\components\RunHistoryView.jsx" 2>nul
del /q "frontend\src\components\SettingsPanelHotfix.jsx" 2>nul
del /q "frontend\src\components\SettingsView.jsx" 2>nul
del /q "frontend\src\components\Sidebar.jsx" 2>nul
del /q "frontend\src\components\SourcesPanel.jsx" 2>nul
del /q "frontend\src\components\StabilizationPreflightPanel.jsx" 2>nul
del /q "frontend\src\components\SupervisorAutoApplyPanel.jsx" 2>nul
del /q "frontend\src\components\SupervisorPanel.jsx" 2>nul
del /q "frontend\src\components\SupervisorView.jsx" 2>nul
del /q "frontend\src\components\TaskHistoryPanel.jsx" 2>nul
del /q "frontend\src\components\TaskRunnerPanel.jsx" 2>nul
del /q "frontend\src\components\TimelinePanel.jsx" 2>nul
del /q "frontend\src\components\ToolStreamPanel.jsx" 2>nul
del /q "frontend\src\components\WorkspaceShell.jsx" 2>nul

REM === FRONTEND: мёртвые API ===
del /q "frontend\src\api\agentRuntime.js" 2>nul
del /q "frontend\src\api\api.js" 2>nul
del /q "frontend\src\api\autodev.js" 2>nul
del /q "frontend\src\api\desktop.js" 2>nul
del /q "frontend\src\api\desktop_lifecycle.js" 2>nul
del /q "frontend\src\api\multi_agent.js" 2>nul
del /q "frontend\src\api\phase10.js" 2>nul
del /q "frontend\src\api\phase11.js" 2>nul
del /q "frontend\src\api\phase12.js" 2>nul
del /q "frontend\src\api\project_brain.js" 2>nul
del /q "frontend\src\api\project_patch.js" 2>nul
del /q "frontend\src\api\run_history.js" 2>nul
del /q "frontend\src\api\supervisor.js" 2>nul

REM === FRONTEND: мёртвые стили ===
del /q "frontend\src\styles_patch.css" 2>nul
del /q "frontend\src\styles\chat_layout_patch.css" 2>nul

REM === BACKEND: мёртвые данные ===
del /q "backend\data\execution_history\*.*" 2>nul
rmdir /q "backend\data\execution_history" 2>nul
rmdir /q "backend\.jarvis_chat_uploads" 2>nul

echo.
echo   Готово! Удалены ~140 мёртвых файлов.
echo   Проект стал чистым.
echo.
pause
