#!/usr/bin/env bash
# ============================================================================
# Run the analysis with REAL semantic embeddings (PatentSBERTa) + BERTopic
# instead of the TF-IDF / KMeans fallback.
#
# Use this where you have internet (to download the model) and ideally a GPU.
# It is a drop-in: config.yaml already points at PatentSBERTa and BERTopic, so
# once these packages are present the pipeline auto-selects them.
# ============================================================================
set -euo pipefail

echo ">> Installing semantic stack (this downloads PyTorch + models, ~2-3 GB)…"
pip install --quiet \
    "sentence-transformers>=2.2" \
    "bertopic>=0.16" \
    "umap-learn>=0.5" \
    "hdbscan>=0.8" \
    "scikit-learn>=1.3"

echo ">> Running pipeline with semantic embeddings…"
# PatentSBERTa is downloaded on first use and cached under ~/.cache/huggingface.
python run.py "$@"

echo ">> Done. The report header will now read 'technology discovery via bertopic'."
