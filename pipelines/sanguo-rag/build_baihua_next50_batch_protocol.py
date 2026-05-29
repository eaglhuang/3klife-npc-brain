from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-baihua-bootstrap-lane.json"
DEFAULT_WAVE001_SUMMARY_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/top50-bootstrap-wave-001-summary.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/protocol"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build wave-002+ batch SOP from wave-001 rehearsal outputs.")
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--wave001-summary-path", default=str(DEFAULT_WAVE001_SUMMARY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--sop-file-name", default="wave-002-plus-sop.zh-TW.md")
    parser.add_argument("--run-template-file-name", default="wave-002-run-template.json")
    parser.add_argument("--checkpoint-template-file-name", default="wave-002-checkpoint-template.json")
    parser.add_argument("--protocol-summary-file-name", default="wave-002-plus-protocol-summary.json")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_sop(*, protocol: dict[str, Any], wave001_summary: dict[str, Any], run_template_path: Path, checkpoint_template_path: Path) -> str:
    required_outputs = protocol.get("requiredWaveOutputs") if isinstance(protocol.get("requiredWaveOutputs"), list) else []
    wave_size = int(protocol.get("defaultWaveSize") or 50)
    metrics = wave001_summary.get("metrics") if isinstance(wave001_summary.get("metrics"), dict) else {}
    whitelist_count = int(metrics.get("whitelistCandidateCount") or 0)
    conflict_count = int(metrics.get("conflictCount") or 0)

    lines: list[str] = []
    lines.append("# 三國白話 Bootstrap Wave-002+ 批次 SOP")
    lines.append("")
    lines.append(f"- GeneratedAt: `{utc_now()}`")
    lines.append(f"- 預設每批人數: `{wave_size}`")
    lines.append(f"- Wave-001 參考：whitelist=`{whitelist_count}`、conflict=`{conflict_count}`")
    lines.append("")
    lines.append("## 目標")
    lines.append("")
    lines.append("1. 以固定 50 人批次重複 wave-by-wave 流程。")
    lines.append("2. 每批都必須能重跑、續跑、回放。")
    lines.append("3. 保持 canonicalWrites=false，僅輸出候選與審核材料。")
    lines.append("")
    lines.append("## 每批執行步驟")
    lines.append("")
    lines.append("1. 產生 job manifest")
    lines.append("2. 建立 passage bundles")
    lines.append("3. 跑 focus relationship runner")
    lines.append("4. merge / normalize")
    lines.append("5. conflict checker")
    lines.append("6. trust-zone review lane adapter")
    lines.append("7. human review markdown + template")
    lines.append("8. pilot rehearsal summary")
    lines.append("")
    lines.append("## 命令樣板")
    lines.append("")
    lines.append("```powershell")
    lines.append("python pipelines/sanguo-rag/build_baihua_top50_bootstrap_jobs.py --wave-id <wave-id> --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --top 50 --overwrite")
    lines.append("python pipelines/sanguo-rag/build_baihua_passage_bundles.py --wave-id <wave-id> --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --overwrite")
    lines.append("python pipelines/sanguo-rag/run_baihua_focus_relationship_runner.py --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --overwrite")
    lines.append("python pipelines/sanguo-rag/merge_baihua_bootstrap_candidates.py --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --overwrite --wave-id <wave-id>")
    lines.append("python pipelines/sanguo-rag/check_baihua_bootstrap_conflicts.py --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --overwrite")
    lines.append("python pipelines/sanguo-rag/adapt_baihua_bootstrap_to_trust_zone.py --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --overwrite")
    lines.append("python pipelines/sanguo-rag/render_baihua_bootstrap_human_review.py --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --overwrite")
    lines.append("python pipelines/sanguo-rag/run_baihua_top50_pilot_rehearsal.py --output-root artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/<wave-id> --overwrite")
    lines.append("```")
    lines.append("")
    lines.append("## Required Outputs")
    lines.append("")
    for item in required_outputs:
        lines.append(f"- `{item}`")
    lines.append("")
    lines.append("## 交付與關卡")
    lines.append("")
    lines.append("1. 每批至少提交 `summary.json + summary.md + human-review md + decisions template`。")
    lines.append("2. 若 conflict 增加，先停在 reviewer/human lane，不得直接升級 whitelist。")
    lines.append("3. 每批完成後更新 checkpoint（見下列 JSON 模板）。")
    lines.append("")
    lines.append(f"- Run 模板：`{run_template_path}`")
    lines.append(f"- Checkpoint 模板：`{checkpoint_template_path}`")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    policy_path = Path(args.policy_path).resolve()
    wave001_summary_path = Path(args.wave001_summary_path).resolve()
    output_root = Path(args.output_root).resolve()
    sop_path = output_root / args.sop_file_name
    run_template_path = output_root / args.run_template_file_name
    checkpoint_template_path = output_root / args.checkpoint_template_file_name
    protocol_summary_path = output_root / args.protocol_summary_file_name

    if not args.overwrite and any(path.exists() for path in [sop_path, run_template_path, checkpoint_template_path, protocol_summary_path]):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {sop_path}")

    policy = read_json(policy_path)
    wave_protocol = policy.get("waveProtocol") if isinstance(policy.get("waveProtocol"), dict) else {}
    wave001_summary = read_json(wave001_summary_path)

    run_template = {
        "version": "1.0.0",
        "mode": "baihua-bootstrap-wave-run-template",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "waveId": "wave-002",
        "waveTop": int(wave_protocol.get("defaultWaveSize") or 50),
        "rankingPath": "",
        "outputRoot": "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-002",
        "steps": [
            {"id": "0101-jobs", "command": "build_baihua_top50_bootstrap_jobs.py", "required": True},
            {"id": "0102-bundles", "command": "build_baihua_passage_bundles.py", "required": True},
            {"id": "0201-runner", "command": "run_baihua_focus_relationship_runner.py", "required": True},
            {"id": "0202-merge", "command": "merge_baihua_bootstrap_candidates.py --wave-id <wave-id>", "required": True},
            {"id": "0203-conflict", "command": "check_baihua_bootstrap_conflicts.py", "required": True},
            {"id": "0301-adapter", "command": "adapt_baihua_bootstrap_to_trust_zone.py", "required": True},
            {"id": "0302-human-review", "command": "render_baihua_bootstrap_human_review.py", "required": True},
            {"id": "0401-rehearsal", "command": "run_baihua_top50_pilot_rehearsal.py", "required": True},
        ],
    }
    write_json(run_template_path, run_template)

    checkpoint_template = {
        "version": "1.0.0",
        "mode": "baihua-bootstrap-wave-checkpoint-template",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "waveId": "wave-002",
        "status": "pending|running|completed|blocked",
        "metrics": {
            "jobCount": 0,
            "candidateCount": 0,
            "reviewLaneCount": 0,
            "conflictCount": 0,
            "whitelistCandidateCount": 0,
            "blacklistCandidateCount": 0,
        },
        "requiredOutputs": wave_protocol.get("requiredWaveOutputs") if isinstance(wave_protocol.get("requiredWaveOutputs"), list) else [],
        "notes": [],
    }
    write_json(checkpoint_template_path, checkpoint_template)

    sop_text = render_sop(
        protocol=wave_protocol,
        wave001_summary=wave001_summary,
        run_template_path=run_template_path,
        checkpoint_template_path=checkpoint_template_path,
    )
    sop_path.parent.mkdir(parents=True, exist_ok=True)
    sop_path.write_text(sop_text, encoding="utf-8")

    protocol_summary = {
        "mode": "baihua-bootstrap-next50-batch-protocol",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "policyPath": str(policy_path),
            "wave001SummaryPath": str(wave001_summary_path),
        },
        "outputs": {
            "sopPath": str(sop_path),
            "runTemplatePath": str(run_template_path),
            "checkpointTemplatePath": str(checkpoint_template_path),
            "protocolSummaryPath": str(protocol_summary_path),
        },
        "waveProtocol": wave_protocol,
    }
    write_json(protocol_summary_path, protocol_summary)

    print(f"[build_baihua_next50_batch_protocol] wrote {sop_path}")
    print(f"[build_baihua_next50_batch_protocol] wrote {run_template_path}")
    print(f"[build_baihua_next50_batch_protocol] wrote {checkpoint_template_path}")
    print(f"[build_baihua_next50_batch_protocol] wrote {protocol_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
