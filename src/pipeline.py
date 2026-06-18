"""End-to-end orchestration: raw exports -> screened corpus -> report."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from . import ingest, filter_pcr, segment, classify, discover, report
from .embed import Embedder


def run(config_path: str = "config.yaml") -> Path:
    cfg = Config.load(config_path)
    paths = cfg["paths"]
    Path(paths["interim_dir"]).mkdir(parents=True, exist_ok=True)

    print("[1/6] Ingesting raw patent exports …")
    raw = ingest.load_raw(paths["raw_dir"])

    print("[2/6] Screening for PCR + zero-degree relevance …")
    patents = filter_pcr.filter_corpus(raw, cfg.raw)
    patents.to_csv(Path(paths["interim_dir"]) / "screened.csv", index=False)
    if patents.empty:
        raise RuntimeError("No relevant patents after screening — loosen filters "
                           "in config.yaml or check the exports.")

    print("[3/6] Segmenting into passages …")
    passages = segment.to_passages(patents, cfg["embedding"]["passage_max_chars"])
    passages = classify.tag_categories(passages)

    print("[4/6] Embedding passages …")
    emb_cfg = cfg["embedding"]
    embedder = Embedder(emb_cfg["model"], emb_cfg["fallback_model"],
                        emb_cfg["batch_size"],
                        use_mean_pooling=emb_cfg.get("use_mean_pooling", True))
    embeddings = embedder.fit_transform(passages["passage"].tolist())
    print(f"  embedding backend: {embedder.backend}")

    print("[5/6] Discovering technology clusters …")
    labels, cluster_terms, method = discover.discover(passages, embeddings, cfg.raw)
    passages["cluster"] = labels
    passages.to_csv(Path(paths["interim_dir"]) / "passages.csv", index=False)

    print("[6/6] Building report …")
    report_path = report.build_report(
        patents, passages, cluster_terms, method, cfg.raw, paths["output_dir"]
    )
    print(f"\nDone. Open: {report_path}")
    return report_path
