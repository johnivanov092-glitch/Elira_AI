"""Thin facade — all profile service logic lives in application/users/profile_service.py."""
from app.application.users.profile_service import (  # noqa: F401
    create_profile,
    get_profiles,
    remove_profile,
)
