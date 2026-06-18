#!/usr/bin/env bash
# ============================================================================
# Run the analysis with PatentBERT embeddings (config.yaml: anferico/bert-for-
# patents, mean-pooled) + BERTopic, instead of the TF-IDF / KMeans fallback.
#
# Requirements:
#   * internet access to PyPI  AND  HuggingFace (huggingface.co) for the model
#   * ideally a GPU (CPU works but is slower on ~10k passages)
#
# This is a drop-in: config.yaml already selects PatentBERT, so once the stack
# is installed and the weights download, the pipeline uses it automatically.
# ============================================================================
set -euo pipefail

echo ">> Installing PatentBERT stack (PyTorch + transformers + BERTopic, ~2-3 GB)…"
pip install --quiet \
    "torch" \
    "transformers>=4.30" \
    "sentence-transformers>=2.2" \
    "bertopic>=0.16" \
    "umap-learn>=0.5" \
    "hdbscan>=0.8" \
    "scikit-learn>=1.3"

# Optional: point at a local model dir if HuggingFace is blocked.
#   export HF_HUB_OFFLINE=1
#   then set embedding.model in config.yaml to the local path of the weights.

echo ">> Running pipeline with PatentBERT embeddings…"
python run.py "$@"

echo ">> Done. The report header will read 'technology discovery via bertopic'"
echo "   and the embedding backend line will read 'patentbert:anferico/bert-for-patents'."
