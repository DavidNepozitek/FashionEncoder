import argparse
import datetime
import logging
import time

import tensorflow as tf
import src.models.encoder.metrics as metrics
import src.models.encoder.fashion_encoder as fashion_enc
import src.data.input_pipeline as input_pipeline
import src.models.encoder.utils as utils


class EncoderTask:

    def __init__(self, params):
        self.params = params

    @staticmethod
    def fitb(model, dataset, epoch):
        acc = tf.metrics.CategoricalAccuracy()
        mask = tf.constant([[[0]]])  # FITB mask token is placed at 0th index
        preprocessor = model.get_layer("preprocessor")

        for task in dataset:
            EncoderTask.fitb_step(model, preprocessor, task, mask, acc)
        return acc.result()

    @staticmethod
    def fitb_step(model, preprocessor, task, mask, acc=None):
        logger = tf.get_logger()
        inputs, input_categories, targets, target_categories, target_position = task
        logger.debug("Targets")
        logger.debug(targets)
        _, targets = preprocessor([targets, target_categories, None], training=False)
        res = model([inputs, input_categories, mask], training=False)
        outputs = res[0]

        logger.debug("Processed targets")
        logger.debug(targets)
        logger.debug("Outputs")
        logger.debug(outputs)

        metrics.fitb_acc(outputs, targets, mask, target_position, input_categories, acc)

    def debug(self):
        # Create the model
        tf.config.experimental_run_functions_eagerly(True)
        model = fashion_enc.create_model(self.params, is_train=False)
        model.summary()

        if "checkpoint_dir" in self.params:
            ckpt = tf.train.Checkpoint(step=tf.Variable(1), model=model)
            manager = tf.train.CheckpointManager(ckpt, self.params["checkpoint_dir"], max_to_keep=3)
            ckpt.restore(manager.latest_checkpoint)
            if manager.latest_checkpoint:
                print("Restored from {}".format(manager.latest_checkpoint), flush=True)
            else:
                print("Initializing from scratch.", flush=True)

        logger = tf.get_logger()
        logger.setLevel(logging.DEBUG)

        current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        train_log_dir = 'logs/' + current_time + '/debug'
        debug_summary_writer = tf.summary.create_file_writer(train_log_dir)

        train_dataset, fitb_dataset = self.get_datasets()
        train_dataset = train_dataset.take(1)
        fitb_dataset = fitb_dataset.take(1)

        logger.debug("-------------- FITB TRACE --------------")
        mask = tf.constant([[[0]]])
        preprocessor = model.get_layer("preprocessor")
        task = tf.data.experimental.get_single_element(fitb_dataset)

        tf.summary.trace_on(graph=True)
        EncoderTask.fitb_step(model, preprocessor, task, mask)
        with debug_summary_writer.as_default():
            tf.summary.trace_export(
                name="fitb_trace",
                step=0)

        logger.debug("-------------- TRAIN TRACE --------------")
        inputs, targets = tf.data.experimental.get_single_element(train_dataset)
        tf.summary.trace_on(graph=True)
        ret = EncoderTask.train_step(model, inputs[0], inputs[1], inputs[2])
        with debug_summary_writer.as_default():
            tf.summary.trace_export(
                name="train_trace",
                step=0)

        logger.debug("-------------- TRAIN METRICS TRACE --------------")
        outputs = ret[0]
        targets = ret[1]
        tf.summary.trace_on(graph=True)
        metrics.xentropy_loss(outputs, targets, inputs[1], inputs[2], debug=True,
                              categorywise_only=self.params["categorywise_train"])
        with debug_summary_writer.as_default():
            tf.summary.trace_export(
                name="train_metrics_trace",
                step=0)

    @staticmethod
    def train_step(model, inputs, input_categories, mask_positions):
        ret = model([inputs, input_categories, mask_positions], training=True)
        return ret[0], ret[1]

    def _grad(self, model: tf.keras.Model, inputs, targets, acc=None, num_replicas=1, stop_targets_gradient=True):
        with tf.GradientTape() as tape:
            ret = EncoderTask.train_step(model, inputs[0], inputs[1], inputs[2])
            outputs = ret[0]
            targets = ret[1]
            if stop_targets_gradient:
                loss_value = metrics.xentropy_loss(
                    outputs, tf.stop_gradient(targets),
                    inputs[1], inputs[2], acc, categorywise_only=self.params["categorywise_train"]) / num_replicas
            else:
                loss_value = metrics.xentropy_loss(
                    outputs, targets,
                    inputs[1], inputs[2], acc, categorywise_only=self.params["categorywise_train"]) / num_replicas
        grad = tape.gradient(loss_value, model.trainable_variables)
        return loss_value, grad

    def get_datasets(self):
        lookup = None

        if self.params["with_category_grouping"]:
            if "category_file" in self.params:
                lookup = utils.build_po_category_lookup_table(self.params["category_file"])
            else:
                lookup = utils.build_category_lookup_table()

        train_dataset = input_pipeline.get_training_dataset(self.params["dataset_files"],
                                                            self.params["batch_size"],
                                                            not self.params["with_cnn"], lookup)

        fitb_dataset = input_pipeline.get_fitb_dataset([self.params["fitb_file"]], not self.params["with_cnn"],
                                                       lookup, self.params["use_mask_category"]).batch(1)

        return train_dataset, fitb_dataset

    def train(self, on_epoch_end=None):
        if on_epoch_end is None:
            on_epoch_end = []

        train_dataset, fitb_dataset = self.get_datasets()

        num_epochs = self.params["epoch_count"]
        optimizer = tf.optimizers.Adam(self.params["learning_rate"])

        # Prepare logging
        current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        train_log_dir = 'logs/' + current_time + '/train'
        train_summary_writer = tf.summary.create_file_writer(train_log_dir)
        batch_number = 0
        if "checkpoint_dir" not in self.params:
            self.params["checkpoint_dir"] = "./logs/" + current_time + "/tf_ckpts"

        # Create the model
        model = fashion_enc.create_model(self.params, True)
        model.summary()

        test_model = fashion_enc.create_model(self.params, False)

        # Threshold of valid acc when target gradient is not stopped
        max_valid = 0

        ckpt = tf.train.Checkpoint(step=tf.Variable(1), optimizer=optimizer, model=model)
        manager = tf.train.CheckpointManager(ckpt, self.params["checkpoint_dir"], max_to_keep=3)
        ckpt.restore(manager.latest_checkpoint)
        if manager.latest_checkpoint:
            print("Restored from {}".format(manager.latest_checkpoint), flush=True)
        else:
            print("Initializing from scratch.", flush=True)

        if "early_stop" in self.params and self.params["early_stop"]:
            early_stopping_monitor = utils.EarlyStoppingMonitor(self.params["early_stop_patience"],
                                                                self.params["early_stop_delta"])

        for epoch in range(1, num_epochs + 1):
            epoch_loss_avg = tf.keras.metrics.Mean('epoch_loss')
            train_loss = tf.keras.metrics.Mean('train_loss', dtype=tf.float32)
            categorical_acc = tf.metrics.CategoricalAccuracy()

            # Training loop
            for x, y in train_dataset:
                batch_number = batch_number + 1

                # Optimize the model
                if self.params["target_gradient_from"] == -1:
                    loss_value, grads = self._grad(model, x, y, categorical_acc, stop_targets_gradient=False)
                elif max_valid < self.params["target_gradient_from"]:
                    loss_value, grads = self._grad(model, x, y, categorical_acc)
                else:
                    loss_value, grads = self._grad(model, x, y, categorical_acc, stop_targets_gradient=False)

                optimizer.apply_gradients(zip(grads, model.trainable_variables))

                ckpt.step.assign_add(1)

                # Track progress
                epoch_loss_avg(loss_value)  # Add current batch loss
                train_loss(loss_value)

                with train_summary_writer.as_default():
                    tf.summary.scalar('loss', train_loss.result(), step=batch_number)
                    tf.summary.scalar('batch_acc', categorical_acc.result(), step=batch_number)

            with train_summary_writer.as_default():
                tf.summary.scalar('epoch_loss', epoch_loss_avg.result(), step=epoch)
                tf.summary.scalar('epoch_acc', categorical_acc.result(), step=epoch)

            print("Epoch {:03d}: Loss: {:.3f}, Acc: {:.3f}".format(epoch, epoch_loss_avg.result(),
                                                                   categorical_acc.result()))

            if epoch % 2 == 0:
                weights = model.get_weights()
                test_model.set_weights(weights)
                fitb_res = self.fitb(test_model, fitb_dataset, epoch)
                print("Epoch {:03d}: FITB Acc: {:.3f}".format(epoch, fitb_res), flush=True)

                with train_summary_writer.as_default():
                    tf.summary.scalar('fitb_acc', fitb_res, step=epoch)

                save_path = manager.save()
                print("Saved checkpoint for step {}: {}".format(int(ckpt.step), save_path), flush=True)

                if fitb_res > max_valid:
                    max_valid = fitb_res

                if on_epoch_end is not None:
                    for callback in on_epoch_end:
                        callback(model, fitb_res, epoch)

                if "early_stop" in self.params and self.params["early_stop"]:
                    if early_stopping_monitor.should_stop(fitb_res, 2):
                        print("Stopped the training early. Validation accuracy hasn't improved for {} epochs".format(
                            self.params["early_stop_patience"]))
                        break

        save_path = manager.save()
        print("Saved checkpoint for step {}: {}".format(int(ckpt.step), save_path))
        print("Trained on " + str(batch_number) + " batches in total.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-files", type=str, nargs="+", help="Paths to dataset files")
    parser.add_argument("--test-files", type=str, nargs="+", help="Paths to test dataset files")
    parser.add_argument("--fitb-file", type=str,  help="Path to FITB dataset files")
    parser.add_argument("--batch-size", type=int, help="Batch size")
    parser.add_argument("--filter-size", type=int, help="Transformer filter size")
    parser.add_argument("--epoch-count", type=int, help="Number of epochs")
    parser.add_argument("--mode", type=str, help="Type of action", choices=["train", "debug"], required=True)
    parser.add_argument("--hidden-size", type=int, help="Hidden size")
    parser.add_argument("--num-heads", type=int, help="Number of heads")
    parser.add_argument("--num-hidden-layers", type=int, help="Number of hidden layers")
    parser.add_argument("--checkpoint-dir", type=str, help="Checkpoint directory")
    parser.add_argument("--masking-mode", type=str, help="Mode of sequence masking",
                        choices=["single-token", "category-masking"])
    parser.add_argument("--learning-rate", type=float, help="Optimizer's learning rate")
    parser.add_argument("--valid-batch-size", type=int,
                        help="Batch size of validation dataset (by default the same as batch size)")
    parser.add_argument("--with-cnn", help="Use CNN to extract features from images", action='store_true')
    parser.add_argument("--category-embedding", help="Add learned category embedding to image feature vectors",
                        action='store_true')
    parser.add_argument("--categories-count", type=int, help="Add learned category embedding to image feature vectors")
    parser.add_argument("--with-mask-category-embedding", help="Add category embedding to mask token",
                        action='store_true')
    parser.add_argument("--target-gradient-from", type=int,
                        help="Value of valid accuracy, when gradient is let through target tensors, -1 for stopped "
                             "gradient",
                        default=0)
    parser.add_argument("--info", type=str, help="Additional information about the configuration")
    parser.add_argument("--with-category-grouping", help="Categories are mapped into groups",
                        action='store_true')
    parser.add_argument("--category-dim", type=int, help="Dimension of category embedding")
    parser.add_argument("--category-merge", type=str, help="Mode of category embedding merge with visual features",
                        choices=["add", "multiply", "concat"])
    parser.add_argument("--use-mask-category", help="Use true masked item category in FITB task",
                        action='store_true')
    parser.add_argument("--category-file", type=str, help="Path to polyvore outfits categories")
    parser.add_argument("--categorywise-train", help="Compute loss function only between items from the same category",
                        action='store_true')
    parser.add_argument("--early-stop-patience", type=int, help="Number of epochs to wait for improvement", default=5)
    parser.add_argument("--early-stop-delta", type=float, help="Minimum change to qualify as improvement", default=1)
    parser.add_argument("--early-stop", help="Enable early stopping",
                        action='store_true')

    args = parser.parse_args()

    arg_dict = vars(args)

    filtered = {k: v for k, v in arg_dict.items() if v is not None}

    if "dataset_files" in arg_dict and not isinstance(arg_dict["dataset_files"], list):
        arg_dict["dataset_files"] = [arg_dict["dataset_files"]]

    if "test_files" in arg_dict and not isinstance(arg_dict["test_files"], list):
        arg_dict["test_files"] = [arg_dict["test_files"]]

    if "valid_batch_size" not in arg_dict:
        arg_dict["valid_batch_size"] = arg_dict["batch_size"]

    params = {
        "feature_dim": 2048,
        "dtype": "float32",
        "hidden_size": 2048,
        "extra_decode_length": 0,
        "num_hidden_layers": 1,
        "num_heads": 2,
        "max_length": 10,
        "filter_size": 1024,
        "layer_postprocess_dropout": 0.1,
        "attention_dropout": 0.1,
        "relu_dropout": 0.1,
        "learning_rate": 0.001,
        "category_dim": 1024
    }

    params.update(filtered)

    print(params, flush=True)

    start_time = time.time()

    task = EncoderTask(params)

    if args.mode == "train":
        task.train()
    elif args.mode == "debug":
        task.debug()
    else:
        print("Invalid mode")

    end_time = time.time()
    elapsed = end_time - start_time

    print("Task completed in " + str(elapsed) + " seconds")


if __name__ == "__main__":
    main()
