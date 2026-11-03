def redact_prompt_metadata(*, prompt_version: str, snapshot_hash: str, token_count: int) -> dict:
    return {"prompt_version": prompt_version, "snapshot_hash": snapshot_hash, "token_count": token_count}
