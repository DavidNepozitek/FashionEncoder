import tensorflow as tf


def parse_example_with_features(raw):
    example = tf.io.parse_single_sequence_example(
        raw, sequence_features={
            "categories": tf.io.FixedLenSequenceFeature([], tf.int64),
            "features": tf.io.FixedLenSequenceFeature(2048, tf.float32)
        })
    return example[1]["features"], example[1]["categories"]


def decode_img(img):
    # convert the compressed string to a 3D uint8 tensor
    img = tf.image.decode_jpeg(img, channels=3)
    return tf.keras.applications.inception_v3.preprocess_input(tf.image.resize(img, [299, 299]))


def parse_example_with_images(raw):
    example = tf.io.parse_single_sequence_example(
        raw, sequence_features={
            "categories": tf.io.FixedLenSequenceFeature([], tf.int64),
            "images": tf.io.FixedLenSequenceFeature([], tf.string)
        })

    raw_imgs = example[1]["images"]
    images = tf.map_fn(decode_img, raw_imgs, dtype=tf.float32)
    return images, example[1]["categories"]


def get_dataset(filenames, with_features):
    raw_dataset = tf.data.TFRecordDataset(filenames)
    if with_features:
        return raw_dataset.map(parse_example_with_features)
    else:
        return raw_dataset.map(parse_example_with_images)


def add_random_mask_positions(features, categories):
    seq_length = tf.shape(categories)[0]
    random_position = tf.random.uniform((1,), minval=0, maxval=seq_length, dtype="int32", seed=1)
    token_positions = tf.expand_dims(random_position, 0)
    return features, categories, token_positions


def get_training_dataset(filenames, batch_size, with_features, category_lookup=None):
    """
    Build training-type dataset

    Args:
        filenames: Filenames of the tfrecord data
        batch_size: batch size
        with_features: the files contain extracted features
        category_lookup: optional tf.lookup.StaticHashTable for mapping the categories into high-level groups

    Returns: Training-type dataset, each sample contains (inputs, categories, mask_positions)

    """
    outfits = get_dataset(filenames, with_features)

    if category_lookup is not None:
        outfits = outfits.map(lambda inputs, input_categories:
                              map_training_categories(inputs, input_categories, category_lookup)
                              )

    outfits = outfits.cache()

    outfits = outfits.map(add_random_mask_positions, tf.data.experimental.AUTOTUNE)
    outfits = outfits.shuffle(10000, 1)

    if with_features:
        outfits = outfits.padded_batch(batch_size, ([None, 2048], [None], [None, 1]), drop_remainder=True)
    else:
        outfits = outfits.padded_batch(batch_size, ([None, 299, 299, 3], [None], [None, 1]), drop_remainder=True)

    return outfits\
        .prefetch(tf.data.experimental.AUTOTUNE)


def parse_fitb_with_features(raw):
    example = tf.io.parse_single_sequence_example(
        raw, sequence_features={
            "input_categories": tf.io.FixedLenSequenceFeature([], tf.int64),
            "inputs": tf.io.FixedLenSequenceFeature(2048, tf.float32),
            "target_categories": tf.io.FixedLenSequenceFeature([], tf.int64),
            "targets": tf.io.FixedLenSequenceFeature(2048, tf.float32)
        }, context_features={
            "target_position": tf.io.FixedLenFeature([], dtype=tf.int64)
        })
    return example[1]["inputs"], example[1]["input_categories"], \
           example[1]["targets"], example[1]["target_categories"], \
           example[0]["target_position"]


def parse_fitb_with_images(raw):
    example = tf.io.parse_single_sequence_example(
        raw, sequence_features={
            "input_categories": tf.io.FixedLenSequenceFeature([], tf.int64),
            "inputs": tf.io.FixedLenSequenceFeature([], tf.string),
            "target_categories": tf.io.FixedLenSequenceFeature([], tf.int64),
            "targets": tf.io.FixedLenSequenceFeature([], tf.string)
        }, context_features={
            "target_position": tf.io.FixedLenFeature([], dtype=tf.int64)
        })

    inputs = example[1]["inputs"]
    inputs = tf.map_fn(decode_img, inputs, dtype=tf.float32)

    targets = example[1]["targets"]
    targets = tf.map_fn(decode_img, targets, dtype=tf.float32)

    return inputs, example[1]["input_categories"], \
           targets, example[1]["target_categories"], \
           example[0]["target_position"]


def add_mask_mock(inputs, input_categories, targets, target_categories, target_position, true_mask_category=False):
    """
    Adds mock tensor to inputs at the 0th index
    Adds category 0 to input_categories tensor at the 0th index
    Returns:
        Example ready for FITB task
    """

    masked_input = tf.ones_like(inputs[0])
    masked_input = tf.expand_dims(masked_input, axis=0)
    inputs = tf.concat([masked_input, inputs], axis=0)
    if true_mask_category:
        masked_category = target_categories[0]
        masked_category = tf.expand_dims(masked_category, axis=0)
    else:
        masked_category = tf.constant([1], dtype=tf.int32)
    input_categories = tf.concat([masked_category, input_categories], axis=0)

    return inputs, input_categories, targets, target_categories, target_position


def map_fitb_categories(inputs, input_categories, targets, target_categories, target_position, category_lookup):
    input_categories = category_lookup.lookup(input_categories)
    target_categories = category_lookup.lookup(target_categories)

    return inputs, input_categories, targets, target_categories, target_position


def map_training_categories(inputs, input_categories, category_lookup):
    input_categories = category_lookup.lookup(input_categories)

    return inputs, input_categories


def get_fitb_dataset(filenames, with_features, category_lookup=None, use_mask_category=False):
    """
    Build FITB dataset

    Args:
        filenames: filenames of the tfrecord dataset
        with_features: the files contain extracted features
        category_lookup: optional tf.lookup.StaticHashTable for mapping the categories into high-level groups
        use_mask_category: use true mask category (else category id 1 is used)

    Returns: FITB dataset, each sample contains (inputs, input_categories, targets, target_categories, target_position)
        the mask token is located at position 0

    """
    raw_dataset = tf.data.TFRecordDataset(filenames)
    if with_features:
        dataset = raw_dataset.map(parse_fitb_with_features)
    else:
        dataset = raw_dataset.map(parse_fitb_with_images)

    if category_lookup is not None:
        dataset = dataset.map(lambda inputs, input_categories, targets, target_categories, target_position:
                              map_fitb_categories(
                                  inputs, input_categories, targets, target_categories, target_position, category_lookup
                              ))

    return dataset.map(lambda inputs, input_categories, targets, target_categories, target_position: add_mask_mock(
        inputs, tf.cast(input_categories, dtype=tf.int32), targets, tf.cast(target_categories, dtype=tf.int32),
        target_position, use_mask_category)).cache()
