import argparse
import json
from pathlib import Path
import tensorflow as tf
import src.data.data_utils as utils


def main():
    """
    Extract features from outfit images and save to .tfrecord files
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=str, help="Path to dataset root directory", required=True)
    parser.add_argument("--dataset-filepath", type=str, help="Path to dataset .json file", required=True)
    parser.add_argument("--tfrecord-template", type=str, help="Template for .tfrecord file names", required=True)
    parser.add_argument("--shard-count", type=int, help="Number of .tfrecord files", required=True)
    parser.add_argument("--with-features", help="With CNN features extracted", action='store_true')

    args = parser.parse_args()

    dataset_root = args.dataset_root
    dataset_filepath = args.dataset_filepath
    output_template = args.tfrecord_template
    shard_count = args.shard_count
    with_features = args.with_features
    print("Arguments parsed", flush=True)

    examples = process_dataset(dataset_root, dataset_filepath, with_features=with_features)

    print("Processed " + str(len(examples)) + " examples", flush=True)

    files_per_dataset = shard_count
    outfits_per_file = len(examples) // files_per_dataset

    if len(examples) % files_per_dataset > 0:
        outfits_per_file = outfits_per_file + 1

    writer = None

    for i in range(len(examples)):
        if i % outfits_per_file == 0:
            if i != 0:
                if writer is not None:
                    writer.close()
            writer = tf.io.TFRecordWriter(
                output_template.format(
                    i // outfits_per_file, files_per_dataset - 1))
        writer.write(examples[i])
        if i == len(examples):
            if writer is not None:
                writer.close()
    print("Saved the dataset successfully", flush=True)


def process_dataset(dataset_root, dataset_filepath, with_features: bool = False, model_path=None):
    with open(Path(dataset_root, "polyvore_item_metadata.json")) as json_file:
        metadata = json.load(json_file)
    with open(Path(dataset_filepath)) as json_file:
        raw_json = json.load(json_file)
        print("Loaded " + str(len(raw_json)) + " items", flush=True)
        examples = []

        if with_features:
            if model_path is not None:
                model = tf.keras.models.load_model(model_path)
            else:
                model = tf.keras.applications.inception_v3.InceptionV3(weights="imagenet", include_top=False, pooling="avg")

        for outfit in raw_json:
            images = []
            categories = []
            ids = []

            for item in outfit["items"]:
                ids.append(int(item["item_id"]))
                image_path = Path(dataset_root, "images", str(item["item_id"]) + ".jpg")
                if with_features:
                    features = utils.extract_features(model, image_path)
                    images.append(features)
                else:
                    with open(image_path, "rb") as img_file:
                        raw_image = img_file.read()
                    images.append(raw_image)

                categories.append(int(metadata[item["item_id"]]["category_id"]))

            if with_features:
                outfit_features = {
                    "categories": tf.train.FeatureList(feature=[utils.int64_feature(f) for f in categories]),
                    "ids": tf.train.FeatureList(feature=[utils.int64_feature(f) for f in ids]),
                    "features": tf.train.FeatureList(
                        feature=[tf.train.Feature(float_list=tf.train.FloatList(value=f)) for f in images])
                }
            else:
                outfit_features = {
                    "categories": tf.train.FeatureList(feature=[utils.int64_feature(f) for f in categories]),
                    "ids": tf.train.FeatureList(feature=[utils.int64_feature(f) for f in ids]),
                    "images": tf.train.FeatureList(feature=[utils.bytes_feature(f) for f in images])
                }

            feature_lists = tf.train.FeatureLists(feature_list=outfit_features)

            example = tf.train.SequenceExample(feature_lists=feature_lists)
            examples.append(example.SerializeToString())

    return examples


if __name__ == "__main__":
    main()
