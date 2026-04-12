from __future__ import annotations


def append_timeline(
    timeline: list[dict[str, object]],
    step: str,
    title: str,
    status: str,
    detail: str,
) -> None:
    timeline.append(
        {
            "step": step,
            "title": title,
            "status": status,
            "detail": detail,
        }
    )
