__author__ = 'Charlie'
# Placeholder for implementation of justins generative neural style

import tensorflow as tf
import numpy as np
import scipy.io
import scipy.misc
from datetime import datetime

import os, sys, inspect

utils_path = os.path.realpath(
    os.path.abspath(os.path.join(os.path.split(inspect.getfile(inspect.currentframe()))[0], "..")))
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)
import TensorflowUtils as utils

FLAGS = tf.flags.FLAGS
tf.flags.DEFINE_string("model_dir", "Models_zoo/", """Path to the VGG model mat file""")
tf.flags.DEFINE_string("data_dir", "Data_zoo/CIFAR10_data/", """Path to the CIFAR10 data""")
tf.flags.DEFINE_string("style_path", "", """Path to style image to use""")
tf.flags.DEFINE_string("mode", "train", "Network mode train/ test")
tf.flags.DEFINE_string("test_image_path", "", "Path to test image - read only if mode is test")

tf.flags.DEFINE_string("log_dir", "logs/GenerativeNeural_style/", """Path to save logs and checkpoint if needed""")

MODEL_URL = 'http://www.vlfeat.org/matconvnet/models/beta16/imagenet-vgg-verydeep-19.mat'

DATA_URL = 'http://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz'

CONTENT_WEIGHT = 2e-3
CONTENT_LAYER = 'relu2_2'

STYLE_WEIGHT = 2e-1
STYLE_LAYERS = ('relu1_2', 'relu2_2', 'relu3_3', 'relu4_3')

VARIATION_WEIGHT = 1e-4

LEARNING_RATE = 1e-3
MAX_ITERATIONS = 30000

NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN = 20000

IMAGE_SIZE = 32


def activation_summary(x):
    tf.histogram_summary(x.op.name + '/activations', x)
    tf.scalar_summary(x.op.name + '/sparsity', tf.nn.zero_fraction(x))


def get_model_data():
    filename = MODEL_URL.split("/")[-1]
    filepath = os.path.join(FLAGS.model_dir, filename)
    if not os.path.exists(filepath):
        raise IOError("VGG Model not found!")
    data = scipy.io.loadmat(filepath)
    return data


def vgg_net(weights, image):
    layers = (
        'conv1_1', 'relu1_1', 'conv1_2', 'relu1_2', 'pool1',

        'conv2_1', 'relu2_1', 'conv2_2', 'relu2_2', 'pool2',

        'conv3_1', 'relu3_1', 'conv3_2', 'relu3_2', 'conv3_3',
        'relu3_3', 'conv3_4', 'relu3_4', 'pool3',

        'conv4_1', 'relu4_1', 'conv4_2', 'relu4_2', 'conv4_3',
        'relu4_3', 'conv4_4', 'relu4_4', 'pool4',

        'conv5_1', 'relu5_1', 'conv5_2', 'relu5_2', 'conv5_3',
        'relu5_3', 'conv5_4', 'relu5_4'
    )

    net = {}
    current = image
    for i, name in enumerate(layers):
        kind = name[:4]
        if kind == 'conv':
            kernels, bias = weights[i][0][0][0][0]
            # matconvnet: weights are [width, height, in_channels, out_channels]
            # tensorflow: weights are [height, width, in_channels, out_channels]
            kernels = np.transpose(kernels, (1, 0, 2, 3))
            bias = bias.reshape(-1)
            current = utils.conv2d_basic(current, kernels, bias)
        elif kind == 'relu':
            current = tf.nn.relu(current)
        elif kind == 'pool':
            current = utils.avg_pool_2x2(current)
        net[name] = current

    assert len(net) == len(layers)
    return net


def read_cifar10(sess, model_params, filename_queue):
    class CIFAR10Record(object):
        pass

    result = CIFAR10Record()

    label_bytes = 1  # 2 for CIFAR-100
    result.height = IMAGE_SIZE
    result.width = IMAGE_SIZE
    result.depth = 3
    image_bytes = result.height * result.width * result.depth
    record_bytes = label_bytes + image_bytes

    reader = tf.FixedLengthRecordReader(record_bytes=record_bytes)
    result.key, value = reader.read(filename_queue)

    record_bytes = tf.decode_raw(value, tf.uint8)

    depth_major = tf.reshape(tf.slice(record_bytes, [label_bytes], [image_bytes]),
                             [result.depth, result.height, result.width])

    result.image = utils.process_image(tf.transpose(depth_major, [1, 2, 0]), model_params['mean_pixel']).astype(tf.float32)
    result.net = vgg_net(model_params["weights"],
                         tf.reshape(result.image, (1, result.height, result.width, result.depth)))
    result.content_features = sess.run(result.net[CONTENT_LAYER])
    return result


def get_image(image_dir):
    image = scipy.misc.imread(image_dir)
    image = np.ndarray.reshape(image.astype(np.float32), (((1,) + image.shape)))
    return image


def inputs(sess, model_params):
    data_dir = os.path.join(FLAGS.data_dir, 'cifar-10-batches-bin')
    filenames = [os.path.join(data_dir, 'data_batch_%d.bin' % i) for i in xrange(1, 6)]
    for f in filenames:
        if not tf.gfile.Exists(f):
            raise ValueError('Failed to find file: ' + f)

    filename_queue = tf.train.string_input_producer(filenames)

    read_input = read_cifar10(sess, model_params, filename_queue)
    num_preprocess_threads = 16
    min_queue_examples = int(0.4 * NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN)
    input_images, input_content_features = tf.train.shuffle_batch([read_input.image, read_input.content_features],
                                                                  batch_size=FLAGS.batch_size,
                                                                  num_threads=num_preprocess_threads,
                                                                  capacity=min_queue_examples + 3 * FLAGS.batch_size,
                                                                  min_after_dequeue=min_queue_examples)
    return input_images, input_content_features


def inference(input_image):
    W1 = utils.weight_variable([32, 32])
    b1 = utils.bias_variable([32])
    hconv_1 = tf.nn.relu(tf.matmul(input_image,W1) + b1)
    h_norm = utils.batch_norm(hconv_1)
    bottleneck_1 = utils.bottleneck_unit(h_norm,16, 16,down_stride=True,name="res_1")
    bottleneck_2 = utils.bottleneck_unit(bottleneck_1,8, 8, down_stride=True, name="res_2")
    bottleneck_3 = utils.bottleneck_unit(bottleneck_2,16, 16,up_stride=True,name="res_3")
    bottleneck_4 = utils.bottleneck_unit(bottleneck_3,32, 32, up_stride=True, name="res_4")
    W5 = utils.weight_variable([32, 3])
    b5 = utils.bias_variable([3])
    out = tf.nn.tanh(utils.conv2d_basic(bottleneck_4, W5, b5))
    return out


def test(sess, mean_pixel):
    content_image = get_image(FLAGS.test_image_path)
    print content_image.shape
    processed_content = utils.process_image(content_image, mean_pixel)
    best = sess.run(inference(processed_content))
    output = utils.unprocess_image(best.reshape(content_image.shape[1:]), mean_pixel).astype(np.float32)
    scipy.misc.imsave("output.jpg", output)


def main(argv=None):
    utils.maybe_download_and_extract(FLAGS.model_dir, MODEL_URL)
    utils.maybe_download_and_extract(FLAGS.data_dir, DATA_URL, is_tarfile=True)
    model_data = get_model_data()
    model_params = {}

    mean = model_data['normalization'][0][0][0]
    model_params['mean_pixel'] = np.mean(mean, axis=(0, 1))

    model_params['weights'] = np.squeeze(model_data['layers'])

    style_image = get_image(FLAGS.style_path)
    processed_style = utils.process_image(style_image, model_params['mean_pixel']).astype(np.float32)
    style_net = vgg_net(model_params['weights'], processed_style)
    tf.image_summary("Style_Image", style_image)

    with tf.Session() as sess:
        print "Evaluating style features..."
        style_features = {}
        for layer in STYLE_LAYERS:
            features = style_net[layer].eval()
            features = np.reshape(features, (-1, features.shape[3]))
            style_gram = np.matmul(features.T, features) / features.size
            style_features[layer] = style_gram

        print "Reading image inputs"
        input_image, input_content = inputs(sess, model_params)

        print "Setting up inference"
        output_image = inference(input_image)

        print "Calculating various losses"
        image_net = vgg_net(model_params['weights'], output_image)
        content_loss = CONTENT_WEIGHT * tf.nn.l2_loss(image_net[layer] - input_content) / utils.get_tensor_size(
            input_content)

        tf.scalar_summary("Content_loss", content_loss)

        style_losses = []
        for layer in STYLE_LAYERS:
            image_layer = image_net[layer]
            _, height, width, number = map(lambda i: i.value, image_layer.get_shape())
            size = height * width * number
            feats = tf.reshape(image_layer, (-1, number))
            image_gram = tf.matmul(tf.transpose(feats), feats) / size
            style_losses.append(0.5 * tf.nn.l2_loss(image_gram - style_features[layer]))
        style_loss = STYLE_WEIGHT * reduce(tf.add, style_losses)
        tf.scalar_summary("Style_loss", style_loss)

        tv_y_size = utils.get_tensor_size(output_image[:, 1:, :, :])
        tv_x_size = utils.get_tensor_size(output_image[:, :, 1:, :])
        tv_loss = VARIATION_WEIGHT * (
            (tf.nn.l2_loss(output_image[:, 1:, :, :] - output_image[:, :IMAGE_SIZE - 1, :, :]) /
             tv_y_size) +
            (tf.nn.l2_loss(output_image[:, :, 1:, :] - output_image[:, :, :IMAGE_SIZE - 1, :]) /
             tv_x_size))
        tf.scalar_summary("Variation_loss", tv_loss)

        loss = content_loss + style_loss + tv_loss
        tf.scalar_summary("Total_loss", loss)

        train_step = tf.train.AdamOptimizer(LEARNING_RATE).minimize(loss)

        summary_writer = tf.train.SummaryWriter(FLAGS.log_dir, sess.graph_def)
        summary_op = tf.merge_all_summaries()

        sess.run(tf.initialize_all_variables())

        saver = tf.train.Saver()
        ckpt = tf.train.get_checkpoint_state(FLAGS.log_dir)
        if ckpt and ckpt.model_checkpoint_path:
            saver.restore(sess, ckpt.model_checkpoint_path)

        if FLAGS.mode == "test":
            test(sess, model_params['mean_pixel'])
            return

        for step in range(1, MAX_ITERATIONS):
            train_step.run()

            if step % 100 == 0 or step == MAX_ITERATIONS - 1:
                this_loss, summary_str = sess.run([loss, summary_op])
                summary_writer.add_summary(summary_str, global_step=step)

                print('%s : Step %d' % (datetime.now(), step)),
                print('    total loss: %g' % this_loss)

            if step % 1000 == 0 or step == MAX_ITERATIONS - 1:
                print('content loss: %g' % content_loss.eval()),
                print('  style loss: %g' % style_loss.eval()),
                print('     tv loss: %g' % tv_loss.eval())
                saver.save(sess, FLAGS.log_dir + "model.ckpt", global_step=step)


if __name__ == "__main__":
    tf.app.run()