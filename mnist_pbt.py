"""
A convolutional neural network for MNIST that is compatible with
population-based training.
"""

from typing import Any, List
import math
import random
import tensorflow as tf
from pbt import Device, PBTAbleGraph
from mnist_convnet import MNISTConvNet


def random_perturbation(value: float, factor: float, min_val: float = None, max_val: float = None) -> float:
    """
    Returns <value> randomly multiplied or divided by <factor>. If <min_val> is
    not None, the returned value will be no smaller than it. If <max_val> is
    not None, the returned value will be no larger than it.
    """
    if random.random() < 0.5:
        value *= factor
    else:
        value /= factor
    if min_val is not None:
        value = max(value, min_val)
    if max_val is not None:
        value = min(value, max_val)
    return value


class NetUpdate:
    """
    Stores information about a PBTAbleMNISTConvNet's update of its
    hyperparameters.
    """

    prev: 'NetUpdate'
    step_num: int
    learning_rate: float
    keep_prob: float

    def __init__(self, net: 'PBTAbleMNISTConvNet') -> None:
        """
        Creates a new NetUpdate that stores <net>'s current information.
        """
        self.prev = net.last_update
        self.step_num = net.step_num
        self.learning_rate = net.run(net.learning_rate)
        self.keep_prob = net.keep_prob


num_nets = 0


class PBTAbleMNISTConvNet(PBTAbleGraph['PBTAbleMNISTConvNet']):
    """
    A PBTAbleGraph version of an MNIST convnet that trains itself to minimize
    cross entropy with a variable learning rate and dropout keep probability.

    A PBTAbleMNISTConvNet draws its training and testing data from a TensorFlow
    MNIST dataset specified in its initializer. The dataset's labels must be in
    one-hot vector format.
    """

    num: int
    vars: List[tf.Variable]
    copyable_vars: List[tf.Variable]
    dataset: Any
    net: MNISTConvNet
    learning_rate: tf.Variable
    keep_prob: float
    train_op: tf.Operation
    step_num: int
    accuracy: float
    update_accuracy: bool
    last_update: NetUpdate

    def __init__(self, device: Device, sess: tf.Session,
                 dataset, learning_rate: float, keep_prob: float) -> None:
        """
        Creates a new PBTAbleMNISTConvNet with device <device>, Session <sess>,
        dataset <dataset>, initial learning rate <learning_rate>, and dropout
        keep probability <keep_prob>.
        """
        global num_nets
        super().__init__(device, sess)
        with tf.device(self.device):
            self.num = num_nets
            num_nets += 1
            self.dataset = dataset
            self.net = MNISTConvNet()
            net_vars = [self.net.w_conv1, self.net.b_conv1, self.net.w_conv2, self.net.b_conv2,
                        self.net.w_fc1, self.net.b_fc1, self.net.w_fc2, self.net.b_fc2]
            self.learning_rate = tf.Variable(learning_rate, trainable=False)
            self.keep_prob = keep_prob
            cross_entropy = tf.reduce_mean(
                tf.nn.softmax_cross_entropy_with_logits(labels=self.net.y_, logits=self.net.y))
            optimizer = tf.train.AdamOptimizer(self.learning_rate)
            self.train_op = optimizer.minimize(cross_entropy)
            self.copyable_vars = list(net_vars)
            self.copyable_vars.extend(optimizer.get_slot(var, name)
                                      for name in optimizer.get_slot_names() for var in net_vars)
            self.copyable_vars.extend(optimizer._get_beta_accumulators())
            self.vars = [self.learning_rate]
            self.vars.extend(self.copyable_vars)
            self.step_num = 0
            self.accuracy = 0
            self.update_accuracy = True
            self.last_update = None

    def initialize_variables(self, sess: tf.Session) -> None:
        sess.run([var.initializer for var in self.vars])
        self._record_update()

    def _record_update(self):
        self.last_update = NetUpdate(self)

    def print_update_history(self):
        """
        Prints this PBTAbleMNISTConvNet's hyperparameter update history to the
        console.
        """
        print('Net', self.num, 'hyperparameter update history:')
        updates = []
        update = self.last_update
        while update is not None:
            updates.append(update)
            update = update.prev
        while len(updates) > 0:
            update = updates.pop()
            print('Step', update.step_num)
            print('Learning rate:', update.learning_rate)
            print('Keep probability:', update.keep_prob)

    def get_accuracy(self) -> float:
        """
        Returns this PBTAbleMNISTConvNet's accuracy score on the MNIST test
        data set.
        """
        if self.update_accuracy:
            self.accuracy = self.run(self.net.accuracy, feed_dict={self.net.x: self.dataset.test.images,
                                                                   self.net.y_: self.dataset.test.labels,
                                                                   self.net.keep_prob: 1})
            self.update_accuracy = False
            print('Net', self.num, 'step', self.step_num, 'accuracy:', self.accuracy)
        return self.accuracy

    def get_metric(self) -> float:
        return self.get_accuracy()

    def _train_step(self) -> None:
        batch = self.dataset.train.next_batch(50)
        self.run(self.train_op, feed_dict={self.net.x: batch[0],
                                           self.net.y_: batch[1],
                                           self.net.keep_prob: self.keep_prob})
        self.update_accuracy = True
        self.step_num += 1

    def train(self) -> None:
        print('Net', self.num, 'starting training run at step', self.step_num)
        self._train_step()
        while self.step_num % 500 != 0:
            self._train_step()
        print('Net', self.num, 'ending training run at step', self.step_num)

    def copy_and_explore(self, net: 'PBTAbleMNISTConvNet'):
        """
        Copies the specified PBTAbleMNISTConvNet, randomly changing the copied
        hyperparameters.
        """
        print('Net', self.num, 'copying net', net.num)
        for i in range(len(self.copyable_vars)):
            self.assign(self.copyable_vars[i], net, net.copyable_vars[i])
        new_learning_rate = net.run(net.learning_rate)
        new_keep_prob = net.keep_prob
        rand = random.randrange(3)
        if rand <= 1:
            new_learning_rate = random_perturbation(new_learning_rate, 1.2, 0.00001, 0.001)
        if rand >= 1:
            new_keep_prob = random_perturbation(new_keep_prob, 1.2, 0.1, 1)
        with tf.device(self.device):
            self.run(self.learning_rate.assign(new_learning_rate))
        self.keep_prob = new_keep_prob
        self.step_num = net.step_num
        self.update_accuracy = True
        self.last_update = net.last_update
        self._record_update()
        print('Net', self.num, 'finished copying')

    def exploit_and_or_explore(self, population: List['PBTAbleMNISTConvNet']) -> None:
        print('Net', self.num, 'ranking nets')
        # Rank population by accuracy
        ranked_pop = sorted(population, key=lambda net: net.get_accuracy())
        print('Net', self.num, 'finished ranking')
        if ranked_pop.index(self) < math.ceil(0.2 * len(ranked_pop)):  # In the bottom 20%?
            # Copy a net from the top 20%
            self.copy_and_explore(
                ranked_pop[random.randrange(math.floor(0.8 * len(ranked_pop)), len(ranked_pop))])

    @staticmethod
    def population_exploit_explore(population: List['PBTAbleMNISTConvNet']) -> None:
        print('Ranking nets')
        # Rank population by accuracy
        ranked_pop = sorted(population, key=lambda net: net.get_accuracy())
        print('Finished ranking')
        # Bottom 20% copies top 20%
        worst_nets = ranked_pop[:math.ceil(0.2 * len(ranked_pop))]
        best_nets = ranked_pop[math.floor(0.8 * len(ranked_pop)):]
        for i in range(len(worst_nets)):
            worst_nets[i].copy_and_explore(best_nets[i])


def random_mnist_convnet(device: Device, sess: tf.Session, dataset) -> PBTAbleMNISTConvNet:
    """
    Returns a new PBTAbleMNISTConvNet with device <device>, Session <sess>,
    dataset <dataset>, and randomized initial variable values.
    """
    return PBTAbleMNISTConvNet(device, sess, dataset,
                               10 ** min(max(random.gauss(-4, 0.5), -5), -3),
                               min(max(random.gauss(0.5, 0.2), 0.1), 1))
