"""Inspect V5 signal-export pickles without treating signals as closed trades."""

import argparse
import json
import zipfile
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator

import joblib
import pandas as pd


TRACKED_COLUMNS = (
    "enter_long",
    "enter_short",
    "enter_tag",
    "beta_regime",
    "absolute_trend",
    "recent_long_pullback_4h",
    "recent_short_pullback_4h",
    "candidate_score_continuation_long",
    "candidate_score_systemic_pullback_short",
)


def iter_frames(value: Any, path: str = "root") -> Iterator[tuple[str, pd.DataFrame]]:
    if isinstance(value, pd.DataFrame):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from iter_frames(child, f"{path}/{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from iter_frames(child, f"{path}/{index}")


def summarize_frame(path: str, frame: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": path,
        "rows": len(frame),
        "columns": [column for column in TRACKED_COLUMNS if column in frame],
    }
    if "enter_tag" in frame:
        summary["enter_tags"] = dict(
            Counter(str(value) for value in frame["enter_tag"].dropna())
        )
    if "beta_regime" in frame:
        summary["regimes"] = dict(
            Counter(str(value) for value in frame["beta_regime"].dropna())
        )
    if "date" in frame and not frame.empty:
        summary["date_min"] = str(pd.to_datetime(frame["date"], utc=True).min())
        summary["date_max"] = str(pd.to_datetime(frame["date"], utc=True).max())
        record_columns = [
            column
            for column in (
                "date",
                "enter_tag",
                "beta_regime",
                "absolute_trend",
                "candidate_score_continuation_long",
                "candidate_score_systemic_pullback_short",
            )
            if column in frame
        ]
        records = frame.loc[:, record_columns].head(20).copy()
        records["date"] = pd.to_datetime(records["date"], utc=True).astype(str)
        summary["sample"] = records.where(pd.notna(records), None).to_dict("records")
    for column in (
        "enter_long",
        "enter_short",
        "recent_long_pullback_4h",
        "recent_short_pullback_4h",
    ):
        if column in frame:
            numeric = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
            summary[f"{column}_positive"] = int((numeric > 0).sum())
    for column in (
        "absolute_trend",
        "candidate_score_continuation_long",
        "candidate_score_systemic_pullback_short",
    ):
        if column in frame:
            numeric = pd.to_numeric(frame[column], errors="coerce").dropna()
            if not numeric.empty:
                summary[f"{column}_min"] = float(numeric.min())
                summary[f"{column}_max"] = float(numeric.max())
    return summary


def inspect_archive(path: Path) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.endswith(("_signals.pkl", "_rejected.pkl", "_exited.pkl"))
        ]
        for member in members:
            payload = joblib.load(BytesIO(archive.read(member)))
            datasets[member] = [
                summarize_frame(frame_path, frame)
                for frame_path, frame in iter_frames(payload)
            ]
    return {"archive": str(path), "datasets": datasets}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect_archive(args.archive), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
