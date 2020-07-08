

BASE = {
    "feature_dim": 2048,
    "dtype": "float32",
    "hidden_size": 1024,
    "num_hidden_layers": 1,
    "num_heads": 16,
    "max_length": 10,
    "filter_size": 1024,
    "layer_postprocess_dropout": 0.1,
    "attention_dropout": 0.1,
    "relu_dropout": 0.1,
    "learning_rate": 0.0005,
    "category_dim": 1024,
    "batch_size": 128,
    "epoch_count": 200,
    "masking_mode": "single-token",
    "categories_count": 50,
    "category_merge": "add",
    "category_embedding": True,
    "use_mask_category": True,
    "with_category_grouping": True,
    "with_mask_category_embedding": True,
    "categorywise_train": True,
    "early_stop_patience": 8,
    "early_stop_delta": 0.002,
    "early_stop": True,
    "with_cnn": False,
    "target_gradient_from": 0,
    "loss": "cross",
    "early_stop_warmup": 20
}

DISTANCE_BASE = BASE.copy()
DISTANCE_BASE.update(
    loss="distance",
    margin="0.5"
)