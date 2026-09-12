"""Small policy resolver shared by rendering and feedback; no model or service calls."""
import json
import math
from pathlib import Path

DEFAULT_PROFILE = Path(__file__).resolve().parents[2] / 'System/audiences/legacy-short-form.json'


def duration_policy(edl=None, edl_path=None):
    """Resolve the house profile or explicit approved EDL override.

    The coordinator copies a project's audience_profile into the EDL. An
    override is explicit planner/user configuration, never analytics inference.
    Relative profile paths are workspace-relative; absolute paths are accepted.
    """
    edl = edl or {}
    raw = edl.get('audience_profile')
    if raw:
        profile_path = Path(raw).expanduser()
        if not profile_path.is_absolute():
            profile_path = DEFAULT_PROFILE.parents[2] / profile_path
    else:
        profile_path = DEFAULT_PROFILE
    profile = json.loads(profile_path.read_text())
    policy = dict(profile['duration'])
    override = edl.get('duration_policy')
    if override is not None:
        if not isinstance(override, dict):
            raise ValueError('duration_policy must be an explicit object')
        policy.update(override)
    preferred, maximum = policy['preferred_ceiling_seconds'], policy['hard_ceiling_seconds']
    for value in (preferred, maximum):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Duration ceilings must be finite positive seconds')
    if preferred > maximum:
        raise ValueError('Preferred duration cannot exceed hard ceiling')
    if policy.get('minimum_seconds') not in (None, 0):
        raise ValueError('Method 2 has no duration floor; do not pad content')
    return {**policy, 'profile_id': profile['id'], 'profile_path': str(profile_path.resolve()),
            'explicit_edl_override': override is not None}
