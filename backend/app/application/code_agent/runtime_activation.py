"""Run-local tool schema visibility over the canonical provider registry.

Activation only selects schemas. Permissions and tool execution remain with
the existing registry and executor; globally running integrations stay hidden
until explicitly selected by this run.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.application.code_agent.capabilities import (
    ALL_BUILTIN_TOOLS,
    builtin_tools_for_groups,
    normalize_capability_groups,
)
from app.application.tool_providers import ToolRegistry, build_runtime_tool_registry


RuntimeActivationState = tuple[
    set[str], set[str], dict[str, str], bool, bool, set[str],
]
RegistryBuilder = Callable[..., ToolRegistry]


def _load_runtime_activation_state(run_id: str) -> RuntimeActivationState:
    """Restore legacy journal keys, falling back to fresh schema visibility."""
    try:
        from app.application.code_agent.run_journal import RunJournal

        stored = RunJournal.load(run_id).state.get("runtime_activation") or {}
    except Exception:
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return (
        {str(value) for value in stored.get("mcp_server_ids") or [] if str(value)},
        {str(value) for value in stored.get("lsp_server_ids") or [] if str(value)},
        {
            str(key): str(value)
            for key, value in (stored.get("mcp_schema_queries") or {}).items()
            if str(key) and str(value)
        }
        if isinstance(stored.get("mcp_schema_queries"), dict)
        else {},
        bool(stored.get("ssh")),
        bool(stored.get("itops")),
        set(normalize_capability_groups(stored.get("capability_groups"))),
    )


@dataclass(frozen=True)
class RuntimeSchemaUpdate:
    """A rebuilt registry and its exact schema/journal views for the coordinator."""

    registry: ToolRegistry
    schemas: list[dict[str, Any]]
    snapshot: dict[str, Any]


@dataclass
class RuntimeActivation:
    """Own integration selections and built-in groups for exactly one run."""

    project_root: Path
    mcp_server_ids: set[str] = field(default_factory=set)
    lsp_server_ids: set[str] = field(default_factory=set)
    mcp_schema_queries: dict[str, str] = field(default_factory=dict)
    ssh_tools_active: bool = False
    itops_tools_active: bool = False
    capability_groups: set[str] = field(default_factory=set)
    requested_builtin_tools: set[str] = field(default_factory=set)
    registry_builder: RegistryBuilder = field(
        default_factory=lambda: build_runtime_tool_registry,
        repr=False,
    )

    @classmethod
    def for_run(
        cls,
        project_root: Path,
        run_id: str,
        base_tools: Collection[str] | None = None,
        *,
        restored_state: RuntimeActivationState | None = None,
        registry_builder: RegistryBuilder | None = None,
    ) -> RuntimeActivation:
        (
            mcp_server_ids,
            lsp_server_ids,
            mcp_schema_queries,
            ssh_tools_active,
            itops_tools_active,
            capability_groups,
        ) = (
            _load_runtime_activation_state(run_id)
            if restored_state is None else restored_state
        )
        return cls(
            project_root=project_root,
            mcp_server_ids=mcp_server_ids,
            lsp_server_ids=lsp_server_ids,
            mcp_schema_queries=mcp_schema_queries,
            ssh_tools_active=ssh_tools_active,
            itops_tools_active=itops_tools_active,
            capability_groups=capability_groups,
            requested_builtin_tools={
                str(name).strip()
                for name in (base_tools or ())
                if str(name).strip() in ALL_BUILTIN_TOOLS
            },
            registry_builder=registry_builder or build_runtime_tool_registry,
        )

    @property
    def has_optional_tools(self) -> bool:
        """Preserve the existing condition for exposing Workflow requests."""
        return bool(
            self.capability_groups
            or self.requested_builtin_tools
            or self.mcp_server_ids
            or self.lsp_server_ids
            or self.ssh_tools_active
            or self.itops_tools_active
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "mcp_server_ids": sorted(self.mcp_server_ids),
            "lsp_server_ids": sorted(self.lsp_server_ids),
            "mcp_schema_queries": dict(sorted(self.mcp_schema_queries.items())),
            "ssh": self.ssh_tools_active,
            "itops": self.itops_tools_active,
            "capability_groups": sorted(self.capability_groups),
        }

    def rebuild(self) -> RuntimeSchemaUpdate:
        builtin_names = set(builtin_tools_for_groups(self.capability_groups))
        # Search/attachment routing contributes targeted schemas via base_tools.
        builtin_names.update(self.requested_builtin_tools)
        registry = self.registry_builder(
            self.project_root,
            include_builtin=True,
            builtin_tool_names=builtin_names,
            mcp_server_ids=self.mcp_server_ids,
            mcp_schema_queries=self.mcp_schema_queries,
            lsp_server_ids=self.lsp_server_ids,
            include_ssh=self.ssh_tools_active,
            include_itops=self.itops_tools_active,
        )
        return RuntimeSchemaUpdate(registry, registry.collect_schemas(), self.snapshot())

    def activate_groups(self, groups: Collection[str] | None) -> bool:
        """Select newly requested planner/evidence groups without rebuilding yet."""
        new_groups = set(normalize_capability_groups(groups)) - self.capability_groups
        if not new_groups:
            return False
        self.capability_groups.update(new_groups)
        return True

    def apply_tool_result(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool_meta: Mapping[str, Any],
        *,
        status: str,
        user_message: str,
    ) -> RuntimeSchemaUpdate | None:
        """Apply confirmed capability/runtime effects at the coordinator's boundary.

        Rebuilding is observable even when the selection is unchanged. Preserve
        the old successful-call behavior instead of deduplicating those builds.
        Workflow pauses call this only after their dedicated branch has returned.
        """
        if status != "ok" or not bool(tool_meta.get("ok", True)):
            return None
        if tool_name == "capability_load":
            group = str(
                tool_meta.get("capability_group") or arguments.get("group") or ""
            ).strip().lower()
            if group not in normalize_capability_groups((group,)):
                return None
            self.capability_groups.add(group)
            return self.rebuild()
        if tool_name != "runtime_control":
            return None

        operation = str(arguments.get("operation") or "").strip().lower()
        config = arguments.get("config")
        server_id = str(
            tool_meta.get("server_id")
            or arguments.get("server_id")
            or (config.get("id") if isinstance(config, dict) else "")
            or ""
        ).strip()
        if operation in {"mcp_start", "mcp_restart", "mcp_tools"} and server_id:
            self.mcp_server_ids.add(server_id)
            self.mcp_schema_queries[server_id] = str(
                arguments.get("query") or user_message
            ).strip()
        elif operation in {"mcp_stop", "mcp_remove"}:
            self.mcp_server_ids.discard(server_id)
            self.mcp_schema_queries.pop(server_id, None)
        elif operation in {"lsp_start", "lsp_restart"} and server_id:
            self.lsp_server_ids.add(server_id)
        elif operation in {"lsp_stop", "lsp_remove"}:
            self.lsp_server_ids.discard(server_id)
        elif operation in {"ssh_hosts", "ssh_set_hosts"}:
            self.ssh_tools_active = True
        elif operation.startswith("itops_"):
            self.itops_tools_active = True
        return self.rebuild()
