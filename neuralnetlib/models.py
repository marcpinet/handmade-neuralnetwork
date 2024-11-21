import inspect
import json
import time
import logging
import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from abc import ABC, abstractmethod

from neuralnetlib.activations import ActivationFunction
from neuralnetlib.callbacks import EarlyStopping
from neuralnetlib.layers import *
from neuralnetlib.losses import *
from neuralnetlib.metrics import Metric
from neuralnetlib.optimizers import Optimizer
from neuralnetlib.preprocessing import PCA, pad_sequences, clip_gradients
from neuralnetlib.utils import shuffle, progress_bar, is_interactive, is_display_available, format_number, History


class BaseModel(ABC):
    def __init__(self, gradient_clip_threshold: float = 5.0,
                 enable_padding: bool = False,
                 padding_size: int = 32,
                 random_state: int | None = None):
        
        self.gradient_clip_threshold = gradient_clip_threshold
        self.enable_padding = enable_padding
        self.padding_size = padding_size
        self.random_state = random_state if random_state is not None else time.time_ns()

    @abstractmethod
    def forward_pass(self, X: np.ndarray, training: bool = True) -> np.ndarray:
        pass

    @abstractmethod
    def backward_pass(self, error: np.ndarray):
        pass

    @abstractmethod
    def train_on_batch(self, x_batch: np.ndarray, y_batch: np.ndarray) -> float:
        pass
        
    @abstractmethod
    def compile(self, loss_function, optimizer, verbose: bool = False):
        pass

    @abstractmethod
    def predict(self, X: np.ndarray, temperature: float = 1.0) -> np.ndarray:
        pass

    @abstractmethod
    def evaluate(self, x_test: np.ndarray, y_test: np.ndarray, batch_size: int = 32) -> tuple:
        pass

    @abstractmethod
    def save(self, filename: str):
        pass

    @classmethod
    @abstractmethod
    def load(cls, filename: str) -> 'BaseModel':
        pass

class Sequential(BaseModel):
    def __init__(self, gradient_clip_threshold: float = 5.0,
                 enable_padding: bool = False,
                 padding_size: int = 32,
                 random_state: int | None = None):
        super().__init__(gradient_clip_threshold, 
                        enable_padding, padding_size, random_state)
        self.layers = []
        self.loss_function = None
        self.optimizer = None
        self.y_true = None
        self.predictions = None

    def __str__(self) -> str:
        model_summary = f'Sequential(gradient_clip_threshold={self.gradient_clip_threshold}, enable_padding={self.enable_padding}, padding_size={self.padding_size}, random_state={self.random_state})\n'
        model_summary += '-------------------------------------------------\n'
        for i, layer in enumerate(self.layers):
            model_summary += f'Layer {i + 1}: {str(layer)}\n'
        model_summary += '-------------------------------------------------\n'
        model_summary += f'Loss function: {str(self.loss_function)}\n'
        model_summary += f'Optimizer: {str(self.optimizer)}\n'
        model_summary += '-------------------------------------------------\n'
        return model_summary

    def summary(self):
        print(str(self))

    def add(self, layer: Layer):
        if not self.layers:
            if not isinstance(layer, Input):
                raise ValueError("The first layer must be an Input layer.")
        else:
            previous_layer = self.layers[-1]
            previous_type = type(previous_layer)
            current_type = type(layer)
            
            if previous_type in incompatibility_dict:
                if current_type in incompatibility_dict[previous_type]:
                    raise ValueError(
                        f"{current_type.__name__} layer cannot follow {previous_type.__name__} layer.")
            
            if isinstance(previous_layer, Attention) and isinstance(layer, Dense):
                previous_layer.return_sequences = False

        self.layers.append(layer)

        activation_attr = getattr(layer, 'activation', getattr(
            layer, 'activation_function', None))
        if activation_attr and not isinstance(layer, Activation):
            if isinstance(activation_attr, str):
                activation = Activation.from_name(activation_attr)
            elif isinstance(activation_attr, ActivationFunction):
                activation = Activation(activation_attr)
            elif isinstance(activation_attr, Activation):
                activation = activation_attr
            else:
                raise ValueError(
                    f"Invalid activation function: {activation_attr}")
            self.layers.append(activation)

    def compile(self, loss_function: LossFunction | str, optimizer: Optimizer | str, verbose: bool = False):
        self.loss_function = loss_function if isinstance(loss_function, LossFunction) else LossFunction.from_name(
            loss_function)
        self.optimizer = optimizer if isinstance(optimizer, Optimizer) else Optimizer.from_name(optimizer)
        if verbose:
            print(str(self))

    def forward_pass(self, X: np.ndarray, training: bool = True) -> np.ndarray:
        if self.enable_padding:
            original_shape = X.shape
            padded_shape = ((original_shape[0] + self.padding_size - 1) //
                            self.padding_size * self.padding_size,) + original_shape[1:]

            if padded_shape != original_shape:
                padded_X = np.zeros(padded_shape, dtype=X.dtype)
                padded_X[:original_shape[0]] = X
                X = padded_X

        for layer in self.layers:
            if isinstance(layer, (Dropout, LSTM, Bidirectional, GRU)):
                X = layer.forward_pass(X, training)
            else:
                X = layer.forward_pass(X)

        if self.enable_padding and padded_shape != original_shape:
            X = X[:original_shape[0]]

        return X

    def backward_pass(self, error: np.ndarray):
        for i, layer in enumerate(reversed(self.layers)):
            if i == 0 and isinstance(layer, Activation):
                if (type(layer.activation_function).__name__ == "Softmax" and
                        isinstance(self.loss_function, CategoricalCrossentropy)):
                    error = self.predictions - self.y_true

                elif (type(layer.activation_function).__name__ == "Sigmoid" and
                      isinstance(self.loss_function, BinaryCrossentropy)):
                    error = (self.predictions - self.y_true) / (self.predictions *
                                                                (1 - self.predictions) + 1e-15)

                elif isinstance(self.loss_function, SparseCategoricalCrossentropy):
                    y_true_one_hot = np.zeros_like(self.predictions)
                    y_true_one_hot[np.arange(len(self.y_true)), self.y_true] = 1
                    error = self.predictions - y_true_one_hot
            else:
                error = clip_gradients(error)
                error = layer.backward_pass(error)

            layer_idx = len(self.layers) - 1 - i

            if isinstance(layer, LSTM):
                cell = layer.cell
                for grad_pair in [(cell.dWf, cell.dbf), (cell.dWi, cell.dbi),
                                  (cell.dWc, cell.dbc), (cell.dWo, cell.dbo)]:
                    weight_grad, bias_grad = grad_pair
                    clipped_weight_grad = clip_gradients(weight_grad)
                    clipped_bias_grad = clip_gradients(bias_grad)

                self.optimizer.update(layer_idx, cell.Wf, clipped_weight_grad,
                                      cell.bf, clipped_bias_grad)
                self.optimizer.update(layer_idx, cell.Wi, clip_gradients(cell.dWi),
                                      cell.bi, clip_gradients(cell.dbi))
                self.optimizer.update(layer_idx, cell.Wc, clip_gradients(cell.dWc),
                                      cell.bc, clip_gradients(cell.dbc))
                self.optimizer.update(layer_idx, cell.Wo, clip_gradients(cell.dWo),
                                      cell.bo, clip_gradients(cell.dbo))

            elif isinstance(layer, GRU):
                cell = layer.cell
                self.optimizer.update(layer_idx, cell.Wz, clip_gradients(cell.dWz),
                                      cell.bz, clip_gradients(cell.dbz))
                self.optimizer.update(layer_idx, cell.Wr, clip_gradients(cell.dWr),
                                      cell.br, clip_gradients(cell.dbr))
                self.optimizer.update(layer_idx, cell.Wh, clip_gradients(cell.dWh),
                                      cell.bh, clip_gradients(cell.dbh))

            elif hasattr(layer, 'weights'):
                clipped_weights_grad = clip_gradients(layer.d_weights)
                if hasattr(layer, 'd_bias'):
                    clipped_bias_grad = clip_gradients(layer.d_bias)
                    self.optimizer.update(layer_idx, layer.weights, clipped_weights_grad,
                                          layer.bias, clipped_bias_grad)
                else:
                    self.optimizer.update(layer_idx, layer.weights, clipped_weights_grad)

    def train_on_batch(self, x_batch: np.ndarray, y_batch: np.ndarray) -> float:
        self.y_true = y_batch
        self.predictions = self.forward_pass(x_batch)
        predictions = self.predictions.copy()
        loss = self.loss_function(y_batch, predictions)
        error = self.loss_function.derivative(y_batch, predictions)

        if error.ndim == 1:
            error = error[:, None]
        elif isinstance(self.layers[-1], (LSTM, Bidirectional, GRU)) and self.layers[-1].return_sequences:
            error = error.reshape(error.shape[0], error.shape[1], -1)

        self.backward_pass(error)
        return loss

    def fit(self, x_train: np.ndarray, y_train: np.ndarray,
            epochs: int,
            batch_size: int | None = None,
            verbose: bool = True,
            metrics: list | None = None,
            random_state: int | None = None,
            validation_data: tuple | None = None,
            callbacks: list = [],
            plot_decision_boundary: bool = False) -> dict:
        """
        Fit the model to the training data.

        Args:
            x_train: Training data
            y_train: Training labels
            epochs: Number of epochs to train the model
            batch_size: Number of samples per gradient update
            verbose: Whether to print training progress
            metrics: List of metric to evaluate the model
            random_state: Random seed for shuffling the data
            validation_data: Tuple of validation data and labels
            callbacks: List of callback objects (e.g., EarlyStopping)
            plot_decision_boundary: Whether to plot the decision boundary
            
        Returns:
            Dictionary containing the training history of metrics (loss and any other metrics)
        """

        history = History({
            'loss': [],
            'val_loss': []
        })

        if plot_decision_boundary and not is_interactive() and not is_display_available():
            raise ValueError("Cannot display the plot. Please run the script in an environment with a display.")

        x_train = np.array(x_train) if not isinstance(x_train, np.ndarray) else x_train
        y_train = np.array(y_train) if not isinstance(y_train, np.ndarray) else y_train

        for layer in self.layers:
            if hasattr(layer, 'random_state'):
                layer.random_state = random_state if random_state is not None else self.random_state

        has_lstm_or_gru = any(isinstance(layer, (LSTM, Bidirectional, GRU)) for layer in self.layers)
        has_embedding = any(isinstance(layer, Embedding) for layer in self.layers)

        if has_lstm_or_gru and not has_embedding:
            if len(x_train.shape) != 3:
                raise ValueError(
                    "Input data must be 3D (batch_size, time_steps, features) for LSTM/GRU layers without Embedding")
        elif has_embedding:
            if len(x_train.shape) != 2:
                raise ValueError("Input data must be 2D (batch_size, sequence_length) when using Embedding layer")

        if validation_data is not None:
            x_test, y_test = validation_data
            x_test = np.array(x_test)
            y_test = np.array(y_test)

        if metrics is not None:
            metrics: list[Metric] = [Metric(m) for m in metrics]
            for metric in metrics:
                history[metric.name] = []
                history[f'val_{metric.name}'] = []

        for layer in self.layers:
            if isinstance(layer, TextVectorization):
                layer.adapt(x_train)
                break

        if callbacks is None:
            callbacks = []

        for callback in callbacks:
            callback.on_train_begin()

        for epoch in range(epochs):
            for callback in callbacks:
                callback.on_epoch_begin(epoch)

            start_time = time.time()
            x_train_shuffled, y_train_shuffled = shuffle(x_train, y_train,
                                                         random_state=random_state if random_state is not None else self.random_state)
            error = 0
            predictions_list = []
            y_true_list = []

            if batch_size is not None:
                num_batches = np.ceil(x_train.shape[0] / batch_size).astype(int)
                for j in range(0, x_train.shape[0], batch_size):
                    x_batch = x_train_shuffled[j:j + batch_size]
                    y_batch = y_train_shuffled[j:j + batch_size]
                    if y_batch.ndim == 1:
                        y_batch = y_batch.reshape(-1, 1)
                    error += self.train_on_batch(x_batch, y_batch)
                    predictions_list.append(self.predictions)
                    y_true_list.append(y_batch)

                    if verbose:
                        metrics_str = ''
                        if metrics is not None:
                            for metric in metrics:
                                metric_value = metric(np.vstack(predictions_list), np.vstack(y_true_list))
                                metrics_str += f'{metric.name}: {format_number(metric_value)} - '
                        progress_bar(j / batch_size + 1, num_batches,
                                     message=f'Epoch {epoch + 1}/{epochs} - loss: {format_number(error / (j / batch_size + 1))} - {metrics_str[:-3]} - {time.time() - start_time:.2f}s')

                error /= num_batches
            else:
                error = self.train_on_batch(x_train, y_train)
                predictions_list.append(self.predictions)
                y_true_list.append(y_train)

                if verbose:
                    metrics_str = ''
                    if metrics is not None:
                        for metric in metrics:
                            metric_value = metric(np.vstack(predictions_list), np.vstack(y_true_list))
                            history[metric.name].append(metric_value)
                            metrics_str += f'{metric.name}: {format_number(metric_value)} - '
                    progress_bar(1, 1,
                                 message=f'Epoch {epoch + 1}/{epochs} - loss: {format_number(error)} - {metrics_str[:-3]} - {time.time() - start_time:.2f}s')

            history['loss'].append(error)

            logs = {'loss': error}
            if metrics is not None:
                for metric in metrics:
                    metric_value = metric(np.vstack(predictions_list), np.vstack(y_true_list))
                    logs[metric.name] = metric_value

            if validation_data is not None:
                x_test, y_test = validation_data
                val_loss, val_predictions = self.evaluate(x_test, y_test, batch_size)
                history['val_loss'].append(val_loss)
                logs['val_loss'] = val_loss

                if metrics is not None:
                    val_metrics = []
                    for metric in metrics:
                        val_metric = metric(val_predictions, y_test)
                        history[f'val_{metric.name}'].append(val_metric)
                        logs[f'val_{metric.name}'] = val_metric
                        val_metrics.append(val_metric)
                    if verbose:
                        val_metrics_str = ' - '.join(
                            f'val_{metric.name}: {format_number(val_metric)}'
                            for metric, val_metric in zip(metrics, val_metrics)
                        )
                        print(f' - {val_metrics_str}', end='')

                val_predictions = None

            stop_training = False
            for callback in callbacks:
                if isinstance(callback, EarlyStopping):
                    if callback.on_epoch_end(epoch, {**logs, 'model': self}):
                        stop_training = True
                        break
                else:
                    callback.on_epoch_end(epoch, logs)

            if verbose:
                print()

            if plot_decision_boundary:
                self.__update_plot(epoch, x_train, y_train,
                                   random_state if random_state is not None else self.random_state)
                plt.pause(0.1)

            if stop_training:
                break

        if plot_decision_boundary:
            plt.show(block=True)

        for callback in callbacks:
            callback.on_train_end()

        if verbose:
            print()

        return history

    def evaluate(self, x_test: np.ndarray, y_test: np.ndarray, batch_size: int = 32) -> tuple:
        total_loss = 0
        num_batches = int(np.ceil(len(x_test) / batch_size))

        predictions_list = []

        for i in range(0, len(x_test), batch_size):
            batch_x = x_test[i:i + batch_size]
            batch_y = y_test[i:i + batch_size]

            batch_predictions = self.forward_pass(batch_x, training=False)
            batch_loss = self.loss_function(batch_y, batch_predictions)

            total_loss += batch_loss
            predictions_list.append(batch_predictions)

            for layer in self.layers:
                if hasattr(layer, 'reset_cache'):
                    layer.reset_cache()

        avg_loss = total_loss / num_batches

        all_predictions = np.vstack(predictions_list)
        predictions_list = None

        try:
            frame = inspect.currentframe()
            calling_frame = frame.f_back
            code = calling_frame.f_code
            if 'single' in code.co_varnames:
                return avg_loss
        except:
            pass
        finally:
            del frame  # to avoid leaking references

        return avg_loss, all_predictions

    def predict(self, X: np.ndarray, temperature: float = 1.0) -> np.ndarray:
        X = np.array(X)
        predictions = self.forward_pass(X, training=False)

        if not np.isclose(temperature, 1.0, rtol=1e-09, atol=1e-09):
            if isinstance(predictions, np.ndarray):
                predictions = np.clip(predictions, 1e-7, 1.0)
                log_preds = np.log(predictions)
                scaled_log_preds = log_preds / temperature
                predictions = np.exp(scaled_log_preds)
                predictions /= np.sum(predictions, axis=-1, keepdims=True)

        return predictions

    def generate_sequence(self,
                          sequence_start: np.ndarray,
                          max_length: int,
                          stop_token: int | None = None,
                          min_length: int | None = None,
                          temperature: float = 1.0) -> np.ndarray:

        current_sequence = sequence_start.copy()

        for _ in range(max_length - sequence_start.shape[1]):
            predictions = self.predict(current_sequence)  # cuz we already apply temperature in this method

            if predictions.ndim == 3:
                next_token_probs = predictions[:, -1, :]
            else:
                next_token_probs = predictions

            if not np.isclose(temperature, 1.0, rtol=1e-09, atol=1e-09):
                next_token_probs = np.clip(next_token_probs, 1e-7, 1.0)
                log_probs = np.log(next_token_probs)
                scaled_log_probs = log_probs / temperature
                next_token_probs = np.exp(scaled_log_probs)
                next_token_probs /= np.sum(next_token_probs, axis=-1, keepdims=True)

            if min_length is not None and current_sequence.shape[1] < min_length:
                if stop_token is not None:
                    next_token_probs[:, stop_token] = 0
                    next_token_probs /= np.sum(next_token_probs, axis=-1, keepdims=True)

            rng = np.random.default_rng(self.random_state)

            next_tokens = []
            for probs in next_token_probs:
                if np.isnan(probs).any() or np.sum(probs) == 0:
                    next_token = rng.integers(0, probs.shape[0])
                else:
                    probs = probs / np.sum(probs)
                    next_token = rng.choice(probs.shape[0], p=probs)
                next_tokens.append(next_token)

            next_tokens = np.array(next_tokens)

            if stop_token is not None:
                if min_length is None or current_sequence.shape[1] >= min_length:
                    if np.all(next_tokens == stop_token):
                        break

            current_sequence = np.hstack([current_sequence, next_tokens.reshape(-1, 1)])

            self.random_state += 1

        return current_sequence

    def save(self, filename: str):
        model_state = {
            'type': 'Sequential',
            'layers': [],
            'gradient_clip_threshold': self.gradient_clip_threshold,
            'enable_padding': self.enable_padding,
            'padding_size': self.padding_size,
            'random_state': self.random_state
        }
        
        for layer in self.layers:
            model_state['layers'].append(layer.get_config())

        if self.loss_function:
            model_state['loss_function'] = self.loss_function.get_config()
        if self.optimizer:
            model_state['optimizer'] = self.optimizer.get_config()

        with open(filename, 'w') as f:
            json.dump(model_state, f, indent=4)

    @classmethod
    def load(cls, filename: str) -> 'Sequential':
        with open(filename, 'r') as f:
            model_state = json.load(f)

        model = cls()

        model_attributes = vars(model)

        for param, value in model_state.items():
            if param in model_attributes:
                setattr(model, param, value)

        model.layers = [
            Layer.from_config(layer_config) for layer_config in model_state.get('layers', [])
        ]

        if 'loss_function' in model_state:
            model.loss_function = LossFunction.from_config(model_state['loss_function'])
        if 'optimizer' in model_state:
            model.optimizer = Optimizer.from_config(model_state['optimizer'])

        return model

    def __update_plot(self, epoch: int, x_train: np.ndarray, y_train: np.ndarray, random_state: int | None) -> None:
        if not plt.fignum_exists(1):
            if matplotlib.get_backend() != "TkAgg":
                matplotlib.use("TkAgg")
                plt.ion()

            fig, ax = plt.subplots(figsize=(8, 6), num=1)
            pca = PCA(n_components=2, random_state=random_state)
            x_train_2d = pca.fit_transform(x_train)
            fig.pca = pca
        else:
            fig = plt.gcf()
            ax = fig.axes[0]
            pca = fig.pca
            x_train_2d = pca.transform(x_train)

        x_min, x_max = x_train_2d[:, 0].min() - 1, x_train_2d[:, 0].max() + 1
        y_min, y_max = x_train_2d[:, 1].min() - 1, x_train_2d[:, 1].max() + 1
        xx, yy = np.meshgrid(np.arange(x_min, x_max, 0.1),
                             np.arange(y_min, y_max, 0.1))

        if y_train.ndim > 1:
            y_train_encoded = np.argmax(y_train, axis=1)
        else:
            y_train_encoded = y_train.ravel()

        ax.clear()

        scatter = ax.scatter(x_train_2d[:, 0], x_train_2d[:, 1], c=y_train_encoded, cmap='viridis', alpha=0.7)

        labels = np.unique(y_train_encoded)
        handles = [
            plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=scatter.cmap(scatter.norm(label)),
                       label=f'Class {label}', markersize=8) for label in labels]
        ax.legend(handles=handles, title='Classes')

        grid_points = np.c_[xx.ravel(), yy.ravel()]
        Z = self.predict(pca.inverse_transform(grid_points))
        if Z.shape[1] > 1:  # Multiclass classification
            Z = np.argmax(Z, axis=1).reshape(xx.shape)
            ax.contourf(xx, yy, Z, alpha=0.2, cmap=plt.cm.RdYlBu, levels=np.arange(Z.max() + 1))
        else:  # Binary classification
            Z = (Z > 0.5).astype(int).reshape(xx.shape)
            ax.contourf(xx, yy, Z, alpha=0.2, cmap=plt.cm.RdYlBu, levels=1)

        ax.set_xlabel("PCA Component 1")
        ax.set_ylabel("PCA Component 2")
        ax.set_title(f"Decision Boundary (Epoch {epoch + 1})")

        fig.canvas.draw()
        plt.pause(0.1)
        

class Autoencoder(BaseModel):
    def __init__(self, 
                 encoder_layers: list = None,
                 decoder_layers: list = None,
                 gradient_clip_threshold: float = 5.0,
                 enable_padding: bool = False,
                 padding_size: int = 32,
                 random_state: int | None = None,
                 skip_connections: bool = False,
                 l1_reg: float = 0.0,
                 l2_reg: float = 0.0,
                 variational: bool = False):
        super().__init__(gradient_clip_threshold, 
                        enable_padding, padding_size, random_state)
        
        self.encoder_layers = encoder_layers if encoder_layers is not None else []
        self.decoder_layers = decoder_layers if decoder_layers is not None else []
        
        self.encoder_optimizer = None
        self.decoder_optimizer = None
        self.encoder_loss = None
        self.decoder_loss = None
        
        self.y_true = None
        self.predictions = None
        self.latent_space = None
        self.latent_mean = None
        self.latent_log_var = None
        self.skip_connections = skip_connections
        
        self.l1_reg = l1_reg
        self.l2_reg = l2_reg
        self.variational = variational
        self.skip_cache = {}
        
        self.epsilon = 1e-7

    def _calculate_kl_divergence(self):
        if not self.variational:
            return 0.0
        kl_loss = -0.5 * np.mean(
            1 + self.latent_log_var - np.square(self.latent_mean) - np.exp(self.latent_log_var)
        )
        return kl_loss

    def _reparameterize(self):
        if not self.variational:
            return self.latent_space
        rng = np.random.default_rng(self.random_state)
        epsilon = rng.normal(size=self.latent_mean.shape)
        return self.latent_mean + np.exp(0.5 * self.latent_log_var) * epsilon
    
    def _calculate_regularization(self):
        reg_loss = 0.0
        
        def process_layer(layer):
            reg = 0.0
            if hasattr(layer, 'weights'):
                if self.l1_reg > 0:
                    reg += self.l1_reg * np.sum(np.abs(layer.weights))
                if self.l2_reg > 0:
                    reg += self.l2_reg * np.sum(np.square(layer.weights))
            return reg
        
        for layer in self.encoder_layers:
            reg_loss += process_layer(layer)
            
        for layer in self.decoder_layers:
            reg_loss += process_layer(layer)
            
        return reg_loss
    
    def _apply_skip_connection(self, current_output: np.ndarray, decoder_idx: int) -> np.ndarray:
        if not self.skip_connections:
            return current_output
            
        encoder_idx = len(self.encoder_layers) - decoder_idx - 2
        
        if encoder_idx < 0 or encoder_idx >= len(self.encoder_layers):
            return current_output
            
        encoder_output = self.skip_cache.get(encoder_idx)
        if encoder_output is None:
            return current_output
            
        if encoder_output.shape == current_output.shape:
            alpha = 0.7
            return alpha * current_output + (1 - alpha) * encoder_output
        else:
            try:
                target_shape = current_output.shape
                if len(encoder_output.shape) == len(target_shape):
                    reshaped_output = np.resize(encoder_output, target_shape)
                    alpha = 0.7
                    return alpha * current_output + (1 - alpha) * reshaped_output
            except ValueError:
                pass
            
            return current_output
    
    def add_encoder_layer(self, layer: Layer):
        if not self.encoder_layers:
            if not isinstance(layer, Input):
                raise ValueError("The first encoder layer must be an Input layer.")
        else:
            previous_layer = self.encoder_layers[-1]
            previous_type = type(previous_layer)
            current_type = type(layer)
            
            if previous_type in incompatibility_dict:
                if current_type in incompatibility_dict[previous_type]:
                    raise ValueError(
                        f"{current_type.__name__} layer cannot follow {previous_type.__name__} layer.")

        self.encoder_layers.append(layer)

        activation_attr = getattr(layer, 'activation', getattr(
            layer, 'activation_function', None))
        if activation_attr and not isinstance(layer, Activation):
            if isinstance(activation_attr, str):
                activation = Activation.from_name(activation_attr)
            elif isinstance(activation_attr, ActivationFunction):
                activation = Activation(activation_attr)
            elif isinstance(activation_attr, Activation):
                activation = activation_attr
            else:
                raise ValueError(f"Invalid activation function: {activation_attr}")
            self.encoder_layers.append(activation)
    
    def add_decoder_layer(self, layer: Layer):
        if self.decoder_layers:
            previous_layer = self.decoder_layers[-1]
            previous_type = type(previous_layer)
            current_type = type(layer)
            
            if previous_type in incompatibility_dict:
                if current_type in incompatibility_dict[previous_type]:
                    raise ValueError(
                        f"{current_type.__name__} layer cannot follow {previous_type.__name__} layer.")
        
        self.decoder_layers.append(layer)

        activation_attr = getattr(layer, 'activation', getattr(
            layer, 'activation_function', None))
        if activation_attr and not isinstance(layer, Activation):
            if isinstance(activation_attr, str):
                activation = Activation.from_name(activation_attr)
            elif isinstance(activation_attr, ActivationFunction):
                activation = Activation(activation_attr)
            elif isinstance(activation_attr, Activation):
                activation = activation_attr
            else:
                raise ValueError(f"Invalid activation function: {activation_attr}")
            self.decoder_layers.append(activation)
    
    def compile(self, 
                encoder_loss: LossFunction | str = None,
                decoder_loss: LossFunction | str = None,
                encoder_optimizer: Optimizer | str = None,
                decoder_optimizer: Optimizer | str = None,
                verbose: bool = False):
        
        if encoder_loss is None:
            encoder_loss = decoder_loss
        if decoder_loss is None:
            decoder_loss = encoder_loss
        if encoder_optimizer is None:
            encoder_optimizer = decoder_optimizer
        if decoder_optimizer is None:
            decoder_optimizer = encoder_optimizer
            
        if encoder_loss is None or encoder_optimizer is None:
            raise ValueError("At least one loss and optimizer must be specified")
            
        self.encoder_loss = encoder_loss if isinstance(encoder_loss, LossFunction) else LossFunction.from_name(encoder_loss)
        self.decoder_loss = decoder_loss if isinstance(decoder_loss, LossFunction) else LossFunction.from_name(decoder_loss)
        self.encoder_optimizer = encoder_optimizer if isinstance(encoder_optimizer, Optimizer) else Optimizer.from_name(encoder_optimizer)
        self.decoder_optimizer = decoder_optimizer if isinstance(decoder_optimizer, Optimizer) else Optimizer.from_name(decoder_optimizer)
        
        if verbose:
            print(str(self))
            
    def forward_pass(self, X: np.ndarray, training: bool = True) -> np.ndarray:
        if self.enable_padding:
            original_shape = X.shape
            padded_shape = ((original_shape[0] + self.padding_size - 1) //
                            self.padding_size * self.padding_size,) + original_shape[1:]

            if padded_shape != original_shape:
                padded_X = np.zeros(padded_shape, dtype=X.dtype)
                padded_X[:original_shape[0]] = X
                X = padded_X
        
        self.encoder_activations = []
        self.decoder_activations = []
        self.skip_cache = {}
        
        # Encoder forward pass
        encoded = X
        for i, layer in enumerate(self.encoder_layers):
            if isinstance(layer, (Dropout, LSTM, Bidirectional, GRU)):
                encoded = layer.forward_pass(encoded, training)
            else:
                encoded = layer.forward_pass(encoded)
            self.encoder_activations.append(encoded)
            
            if self.skip_connections and isinstance(layer, Dense):
                self.skip_cache[layer.units] = encoded
        
        if self.variational:
            latent_dim = encoded.shape[-1] // 2
            self.latent_mean = encoded[:, :latent_dim]
            self.latent_log_var = encoded[:, latent_dim:]
            self.latent_space = self._reparameterize()
        else:
            self.latent_space = encoded
        
        # Decoder forward pass
        decoded = encoded
        
        for layer in self.decoder_layers:
            if isinstance(layer, (Dropout, LSTM, Bidirectional, GRU)):
                decoded = layer.forward_pass(decoded, training)
            else:
                decoded = layer.forward_pass(decoded)
            if self.skip_connections and isinstance(layer, Dense):
                skip_connection = self.skip_cache.get(layer.units)
                if skip_connection is not None:
                    scale_factor = 1.0 / np.sqrt(layer.units)
                    decoded = decoded + scale_factor * skip_connection
                    
            self.decoder_activations.append(decoded)
                
        if self.enable_padding and padded_shape != original_shape:
            decoded = decoded[:original_shape[0]]
            
        return decoded
    
    def backward_pass(self, error: np.ndarray):
        def clip_gradients(gradient: np.ndarray) -> np.ndarray:
            if gradient is None:
                return None
            
            if self.gradient_clip_threshold > 0:
                grad_norm = np.linalg.norm(gradient)
                if grad_norm > self.gradient_clip_threshold:
                    gradient = gradient * (self.gradient_clip_threshold / grad_norm)
                    
                gradient = np.clip(gradient, -10, 10)
                
                batch_std = np.std(gradient) + 1e-8
                gradient = gradient / batch_std
                
            return gradient

        # Decoder backward pass
        for i, layer in enumerate(reversed(self.decoder_layers)):
            if i == 0 and isinstance(layer, Activation):
                if (type(layer.activation_function).__name__ == "Softmax" and
                        isinstance(self.decoder_loss, CategoricalCrossentropy)):
                    error = self.predictions - self.y_true
                elif (type(layer.activation_function).__name__ == "Sigmoid" and
                      isinstance(self.decoder_loss, BinaryCrossentropy)):
                    error = (self.predictions - self.y_true) / (self.predictions *
                                                               (1 - self.predictions) + 1e-15)
            else:
                error = clip_gradients(error)
                error = layer.backward_pass(error)
                
            layer_idx = len(self.decoder_layers) - 1 - i
            
            if isinstance(layer, (LSTM, GRU)):
                self._update_rnn_weights(layer, layer_idx, clip_gradients, self.decoder_optimizer)
            elif hasattr(layer, 'weights'):
                self._update_layer_weights(layer, layer_idx, clip_gradients, self.decoder_optimizer)
        
        # Encoder backward pass
        for i, layer in enumerate(reversed(self.encoder_layers)):
            error = clip_gradients(error)
            error = layer.backward_pass(error)
            
            layer_idx = len(self.encoder_layers) - 1 - i
            
            if isinstance(layer, (LSTM, GRU)):
                self._update_rnn_weights(layer, layer_idx, clip_gradients, self.encoder_optimizer)
            elif hasattr(layer, 'weights'):
                self._update_layer_weights(layer, layer_idx, clip_gradients, self.encoder_optimizer)
                
    def _update_rnn_weights(self, layer, layer_idx: int, clip_gradients, optimizer):
        if isinstance(layer, LSTM):
            cell = layer.cell
            for grad_pair in [(cell.dWf, cell.dbf), (cell.dWi, cell.dbi),
                              (cell.dWc, cell.dbc), (cell.dWo, cell.dbo)]:
                weight_grad, bias_grad = grad_pair
                clipped_weight_grad = clip_gradients(weight_grad)
                clipped_bias_grad = clip_gradients(bias_grad)
                optimizer.update(layer_idx, cell.Wf, clipped_weight_grad,
                                  cell.bf, clipped_bias_grad)
                
        elif isinstance(layer, GRU):
            cell = layer.cell
            optimizer.update(layer_idx, cell.Wz, clip_gradients(cell.dWz),
                              cell.bz, clip_gradients(cell.dbz))
            optimizer.update(layer_idx, cell.Wr, clip_gradients(cell.dWr),
                              cell.br, clip_gradients(cell.dbr))
            optimizer.update(layer_idx, cell.Wh, clip_gradients(cell.dWh),
                              cell.bh, clip_gradients(cell.dbh))
                
    def _update_layer_weights(self, layer, layer_idx: int, clip_gradients, optimizer):
        clipped_weights_grad = clip_gradients(layer.d_weights)
        if hasattr(layer, 'd_bias'):
            clipped_bias_grad = clip_gradients(layer.d_bias)
            optimizer.update(layer_idx, layer.weights, clipped_weights_grad,
                              layer.bias, clipped_bias_grad)
        else:
            optimizer.update(layer_idx, layer.weights, clipped_weights_grad)
    
    def train_on_batch(self, x_batch: np.ndarray, y_batch: np.ndarray = None) -> float:
        if y_batch is None:
            y_batch = x_batch
                
        self.y_true = y_batch
        self.predictions = self.forward_pass(x_batch)
        
        reconstruction_loss = self.decoder_loss(y_batch, self.predictions)
        regularization_loss = self._calculate_regularization()
        kl_loss = self._calculate_kl_divergence() if self.variational else 0
        
        latent_l2 = 0.0001 * np.mean(np.square(self.latent_space))
        latent_std = np.std(self.latent_space, axis=0)
        distribution_penalty = 0.0001 * np.mean(np.abs(latent_std - 1.0))

        if self.skip_connections:
            latent_l2 *= 0.1
            distribution_penalty *= 0.1
        
        beta = 0.01
        total_loss = reconstruction_loss + regularization_loss + latent_l2 + distribution_penalty + beta * kl_loss
        
        error = self.decoder_loss.derivative(y_batch, self.predictions)
        if error.ndim == 1:
            error = error[:, None]
        elif isinstance(self.decoder_layers[-1], (LSTM, Bidirectional, GRU)) and self.decoder_layers[-1].return_sequences:
            error = error.reshape(error.shape[0], error.shape[1], -1)
                
        self.backward_pass(error)
        return total_loss
    
    def predict(self, X: np.ndarray, output_latent: bool = False, temperature: float = 1.0) -> np.ndarray:
        X = np.array(X)
        encoded = X
        for layer in self.encoder_layers:
            encoded = layer.forward_pass(encoded)
            
        if output_latent:
            return encoded
            
        decoded = encoded
        for layer in self.decoder_layers:
            decoded = layer.forward_pass(decoded)
            
        if not np.isclose(temperature, 1.0, rtol=1e-09, atol=1e-09):
            if isinstance(decoded, np.ndarray):
                decoded = np.clip(decoded, 1e-7, 1.0)
                log_preds = np.log(decoded)
                scaled_log_preds = log_preds / temperature
                decoded = np.exp(scaled_log_preds)
                decoded /= np.sum(decoded, axis=-1, keepdims=True)
                
        return decoded
    
    def evaluate(self, x_test: np.ndarray, y_test: np.ndarray = None, batch_size: int = 32) -> tuple:
        if y_test is None:
            y_test = x_test
            
        total_loss = 0
        num_batches = int(np.ceil(len(x_test) / batch_size))
        predictions_list = []
        
        for i in range(0, len(x_test), batch_size):
            batch_x = x_test[i:i + batch_size]
            batch_y = y_test[i:i + batch_size]
            
            batch_predictions = self.forward_pass(batch_x, training=False)
            decoder_loss = self.decoder_loss(batch_y, batch_predictions)
            encoder_loss = self.encoder_loss(batch_y, batch_predictions)
            batch_loss = (decoder_loss + encoder_loss) / 2
            
            total_loss += batch_loss
            predictions_list.append(batch_predictions)
            
        avg_loss = total_loss / num_batches
        all_predictions = np.vstack(predictions_list)
        
        return avg_loss, all_predictions
  
    @classmethod
    def load(cls, filename: str) -> 'Autoencoder':
        with open(filename, 'r') as f:
            model_state = json.load(f)

        model = cls()

        model_attributes = vars(model)

        for param, value in model_state.items():
            if param in model_attributes:
                setattr(model, param, value)

        model.encoder_layers = [
            Layer.from_config(layer_config) for layer_config in model_state.get('encoder_layers', [])
        ]
        model.decoder_layers = [
            Layer.from_config(layer_config) for layer_config in model_state.get('decoder_layers', [])
        ]

        if 'encoder_loss' in model_state:
            model.encoder_loss = LossFunction.from_config(model_state['encoder_loss'])
        if 'decoder_loss' in model_state:
            model.decoder_loss = LossFunction.from_config(model_state['decoder_loss'])
        if 'encoder_optimizer' in model_state:
            model.encoder_optimizer = Optimizer.from_config(model_state['encoder_optimizer'])
        if 'decoder_optimizer' in model_state:
            model.decoder_optimizer = Optimizer.from_config(model_state['decoder_optimizer'])

        return model
        
    def save(self, filename: str):
        model_state = {
            'type': 'Autoencoder',
            'encoder_layers': [],
            'decoder_layers': [],
            'gradient_clip_threshold': self.gradient_clip_threshold,
            'enable_padding': self.enable_padding,
            'padding_size': self.padding_size,
            'random_state': self.random_state,
            'skip_connections': self.skip_connections,
            'l1_reg': self.l1_reg,
            'l2_reg': self.l2_reg
        }
        
        for layer in self.encoder_layers:
            model_state['encoder_layers'].append(layer.get_config())
        for layer in self.decoder_layers:
            model_state['decoder_layers'].append(layer.get_config())
            
        if self.encoder_loss:
            model_state['encoder_loss'] = self.encoder_loss.get_config()
        if self.decoder_loss:
            model_state['decoder_loss'] = self.decoder_loss.get_config()
        if self.encoder_optimizer:
            model_state['encoder_optimizer'] = self.encoder_optimizer.get_config()
        if self.decoder_optimizer:
            model_state['decoder_optimizer'] = self.decoder_optimizer.get_config()
            
        with open(filename, 'w') as f:
            json.dump(model_state, f, indent=4)

    def __str__(self) -> str:
        model_summary = f'Autoencoder(gradient_clip_threshold={self.gradient_clip_threshold}, ' \
                       f'enable_padding={self.enable_padding}, padding_size={self.padding_size}, random_state={self.random_state}, ' \
                       f'skip_connections={self.skip_connections}, l1_reg={self.l1_reg}, l2_reg={self.l2_reg})\n'
        model_summary += '-------------------------------------------------\n'
        model_summary += 'Encoder:\n'
        for i, layer in enumerate(self.encoder_layers):
            model_summary += f'Layer {i + 1}: {str(layer)}\n'
        model_summary += '-------------------------------------------------\n'
        model_summary += 'Decoder:\n'
        for i, layer in enumerate(self.decoder_layers):
            model_summary += f'Layer {i + 1}: {str(layer)}\n'
        model_summary += '-------------------------------------------------\n'
        model_summary += f'Encoder loss function: {str(self.encoder_loss)}\n'
        model_summary += f'Decoder loss function: {str(self.decoder_loss)}\n'
        model_summary += f'Encoder optimizer: {str(self.encoder_optimizer)}\n'
        model_summary += f'Decoder optimizer: {str(self.decoder_optimizer)}\n'
        model_summary += '-------------------------------------------------\n'
        return model_summary
    
    def summary(self):
        print(str(self))
        
    def fit(self, x_train: np.ndarray, 
            epochs: int,
            batch_size: int | None = None,
            verbose: bool = True,
            metrics: list | None = None,
            random_state: int | None = None,
            validation_data: tuple | None = None,
            callbacks: list = []) -> dict:

        history = History({
            'loss': [],
            'val_loss': []
        })

        x_train = np.array(x_train) if not isinstance(x_train, np.ndarray) else x_train

        for layer in self.encoder_layers + self.decoder_layers:
            if hasattr(layer, 'random_state'):
                layer.random_state = random_state if random_state is not None else self.random_state

        has_lstm_or_gru = any(isinstance(layer, (LSTM, Bidirectional, GRU)) 
                            for layer in self.encoder_layers + self.decoder_layers)
        has_embedding = any(isinstance(layer, Embedding) 
                        for layer in self.encoder_layers + self.decoder_layers)

        if has_lstm_or_gru and not has_embedding:
            if len(x_train.shape) != 3:
                raise ValueError(
                    "Input data must be 3D (batch_size, time_steps, features) for LSTM/GRU layers without Embedding")
        elif has_embedding:
            if len(x_train.shape) != 2:
                raise ValueError("Input data must be 2D (batch_size, sequence_length) when using Embedding layer")

        if validation_data is not None:
            x_val, y_val = validation_data if len(validation_data) == 2 else (validation_data[0], validation_data[0])
            x_val = np.array(x_val)
            y_val = np.array(y_val)

        if metrics is not None:
            metrics: list[Metric] = [Metric(m) for m in metrics]
            for metric in metrics:
                history[metric.name] = []
                history[f'val_{metric.name}'] = []

        for layer in self.encoder_layers + self.decoder_layers:
            if isinstance(layer, TextVectorization):
                layer.adapt(x_train)
                break

        if callbacks is None:
            callbacks = []

        for callback in callbacks:
            callback.on_train_begin()

        for epoch in range(epochs):
            for callback in callbacks:
                callback.on_epoch_begin(epoch)

            start_time = time.time()
            y_train = np.zeros_like(x_train)
            x_train_shuffled, _ = shuffle(x_train, y_train, random_state=random_state if random_state is not None else self.random_state)
            
            error = 0
            predictions_list = []
            inputs_list = []

            if batch_size is not None:
                num_batches = np.ceil(x_train.shape[0] / batch_size).astype(int)
                
                for j in range(0, x_train.shape[0], batch_size):
                    x_batch = x_train_shuffled[j:j + batch_size]
                    
                    error += self.train_on_batch(x_batch)
                    predictions_list.append(self.predictions)
                    inputs_list.append(x_batch)

                    if verbose:
                        metrics_str = ''
                        if metrics is not None:
                            for metric in metrics:
                                metric_value = metric(np.vstack(predictions_list), np.vstack(inputs_list))
                                metrics_str += f'{metric.name}: {format_number(metric_value)} - '
                        progress_bar(j / batch_size + 1, num_batches,
                                    message=f'Epoch {epoch + 1}/{epochs} - loss: {format_number(error / (j / batch_size + 1))} - {metrics_str[:-3]} - {time.time() - start_time:.2f}s')

                error /= num_batches
                
            else:
                error = self.train_on_batch(x_train)
                predictions_list.append(self.predictions)
                inputs_list.append(x_train)

                if verbose:
                    metrics_str = ''
                    if metrics is not None:
                        for metric in metrics:
                            metric_value = metric(np.vstack(predictions_list), np.vstack(inputs_list))
                            history[metric.name].append(metric_value)
                            metrics_str += f'{metric.name}: {format_number(metric_value)} - '
                    progress_bar(1, 1,
                                message=f'Epoch {epoch + 1}/{epochs} - loss: {format_number(error)} - {metrics_str[:-3]} - {time.time() - start_time:.2f}s')

            history['loss'].append(error)

            logs = {'loss': error}
            if metrics is not None:
                for metric in metrics:
                    metric_value = metric(np.vstack(predictions_list), np.vstack(inputs_list))
                    logs[metric.name] = metric_value

            if validation_data is not None:
                val_loss, val_predictions = self.evaluate(x_val, y_val, batch_size)
                history['val_loss'].append(val_loss)
                logs['val_loss'] = val_loss

                if metrics is not None:
                    val_metrics = []
                    for metric in metrics:
                        val_metric = metric(val_predictions, x_val)
                        history[f'val_{metric.name}'].append(val_metric)
                        logs[f'val_{metric.name}'] = val_metric
                        val_metrics.append(val_metric)
                    if verbose:
                        val_metrics_str = ' - '.join(
                            f'val_{metric.name}: {format_number(val_metric)}'
                            for metric, val_metric in zip(metrics, val_metrics)
                        )
                        print(f' - {val_metrics_str}', end='')

                val_predictions = None

            stop_training = False
            for callback in callbacks:
                if isinstance(callback, EarlyStopping):
                    if callback.on_epoch_end(epoch, {**logs, 'model': self}):
                        stop_training = True
                        break
                else:
                    callback.on_epoch_end(epoch, logs)

            if verbose:
                print()

            if stop_training:
                break

        for callback in callbacks:
            callback.on_train_end()

        if verbose:
            print()

        return history

    def generate_image(self, x_train: np.ndarray, n_samples: int = 10, seed: int | None = None, n_examples: int = 1000) -> np.ndarray:
        _ = self.forward_pass(x_train[:n_examples])

        latent_mean_stats = np.mean(self.latent_mean, axis=0)
        latent_std_stats = np.exp(0.5 * np.mean(self.latent_log_var, axis=0))

        latent_mean_repeated = np.tile(latent_mean_stats, (n_samples, 1))
        latent_std_repeated = np.tile(latent_std_stats, (n_samples, 1))

        rng = np.random.default_rng(seed if seed is not None else self.random_state)
        noise = rng.standard_normal(size=(n_samples, 32))

        latent_samples = np.concatenate([
            latent_mean_repeated + noise * latent_std_repeated, np.zeros((n_samples, 32))
        ], axis=1)

        generated = latent_samples
        for layer in self.decoder_layers:
            if isinstance(layer, (Dropout, LSTM, Bidirectional, GRU)):
                generated = layer.forward_pass(generated, training=False)
            elif isinstance(layer, Reshape):
                if isinstance(layer.target_shape, list):
                    layer.target_shape = tuple(layer.target_shape)
                generated = layer.forward_pass(generated)
            else:
                generated = layer.forward_pass(generated)

        return generated


class Transformer(BaseModel):
    def __init__(self,
                 src_vocab_size: int,
                 tgt_vocab_size: int,
                 d_model: int = 512,
                 n_heads: int = 8,
                 n_encoder_layers: int = 6,
                 n_decoder_layers: int = 6,
                 d_ff: int = 2048,
                 dropout_rate: float = 0.1,
                 max_sequence_length: int = 512,
                 gradient_clip_threshold: float = 5.0,
                 enable_padding: bool = True,
                 padding_size: int = 32,
                 scale_embeddings: bool = True,
                 random_state: int | None = None,
                ) -> None:
        
        super().__init__(gradient_clip_threshold, 
                        enable_padding, padding_size, random_state)
        
        self.PAD_IDX = 0
        self.SRC_UNK_IDX = 1
        self.SRC_SOS_IDX = 2
        self.SRC_EOS_IDX = 3
        
        self.TGT_UNK_IDX = 1
        self.TGT_SOS_IDX = 2
        self.TGT_EOS_IDX = 3
        
        self.src_vocab_size = src_vocab_size
        self.tgt_vocab_size = tgt_vocab_size
        self.d_model = d_model
        self.n_heads = n_heads
        self.n_encoder_layers = n_encoder_layers
        self.n_decoder_layers = n_decoder_layers
        self.d_ff = d_ff
        self.dropout_rate = dropout_rate
        self.max_sequence_length = max_sequence_length
        self.gradient_norms = {}
        
        self.src_embedding = Embedding(self.src_vocab_size, d_model, input_length=max_sequence_length, random_state=random_state)
        self.tgt_embedding = Embedding(self.tgt_vocab_size, d_model, input_length=max_sequence_length, random_state=random_state)
        
        self.positional_encoding = PositionalEncoding(
            max_sequence_length=max_sequence_length,
            embedding_dim=d_model,
            scale_embeddings=scale_embeddings
        )
        
        self.encoder_dropout = Dropout(dropout_rate, random_state=random_state)
        self.encoder_layers: list = []
        for _ in range(n_encoder_layers):
            encoder_layer = TransformerEncoderLayer(
                d_model=d_model,
                num_heads=n_heads,
                d_ff=d_ff,
                dropout_rate=dropout_rate,
                attention_dropout=dropout_rate,
                random_state=random_state,
            )
            self.encoder_layers.append(encoder_layer)
            
        self.decoder_dropout = Dropout(dropout_rate, random_state=random_state)
        self.decoder_layers: list = []
        for _ in range(n_decoder_layers):
            decoder_layer = TransformerDecoderLayer(
                d_model=d_model,
                num_heads=n_heads,
                d_ff=d_ff,
                dropout_rate=dropout_rate,
                attention_dropout=dropout_rate,
                random_state=random_state
            )
            self.decoder_layers.append(decoder_layer)
            
        self.output_layer = Dense(tgt_vocab_size, random_state=random_state)
        
        self.optimizer = None
        self.loss_function = None

    def create_padding_mask(self, seq: np.ndarray) -> np.ndarray:
        if len(seq.shape) == 1:
            seq = seq[np.newaxis, :]
        mask = (seq == self.PAD_IDX).astype(np.bool_)
        return mask[:, np.newaxis, np.newaxis, :]

    def create_look_ahead_mask(self, size: int) -> np.ndarray:
        mask = np.triu(np.ones((size, size)), k=1).astype(np.bool_)
        return mask[np.newaxis, np.newaxis, :, :]

    def create_masks(self, inp: np.ndarray, tar: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        batch_size = inp.shape[0]
        enc_padding_mask = self.create_padding_mask(inp)
        dec_padding_mask = self.create_padding_mask(inp)
        look_ahead_mask = self.create_look_ahead_mask(tar.shape[1])
        dec_target_padding_mask = self.create_padding_mask(tar)
        
        look_ahead_mask = np.broadcast_to(
            look_ahead_mask, 
            (batch_size, 1, tar.shape[1], tar.shape[1])
        )
        combined_mask = np.logical_or(dec_target_padding_mask, look_ahead_mask)
        
        return enc_padding_mask, combined_mask, dec_padding_mask

    def encode(self, inp: np.ndarray, training: bool = True, mask: np.ndarray | None = None) -> np.ndarray:
        x = self.src_embedding.forward_pass(inp)
        x = self.positional_encoding.forward_pass(x)
        x = self.encoder_dropout.forward_pass(x, training=training)
        
        for encoder_layer in self.encoder_layers:
            x = encoder_layer.forward_pass(x, mask=mask, training=training)
            
        return x

    def decode(self, tar: np.ndarray, enc_output: np.ndarray, training: bool = True, 
            look_ahead_mask: np.ndarray | None = None, 
            padding_mask: np.ndarray | None = None) -> np.ndarray:
        
        x = self.tgt_embedding.forward_pass(tar)
        x = self.positional_encoding.forward_pass(x)
        
        if training:
            x = self.decoder_dropout.forward_pass(x, training=True)
        
        attention_weights = []
        for decoder_layer in self.decoder_layers:
            x = decoder_layer.forward_pass(
                x, enc_output,
                self_attention_mask=look_ahead_mask,
                cross_attention_mask=padding_mask,
                training=training
            )
            attention_weights.append(decoder_layer.self_attention.attention_weights)
        
        self.last_attention_weights = attention_weights
        
        return x

    def forward_pass(self, inputs: tuple[np.ndarray, np.ndarray], training: bool = True) -> np.ndarray:
        inp, tar = inputs
        
        enc_padding_mask, look_ahead_mask, dec_padding_mask = self.create_masks(inp, tar)
        
        enc_output = self.encode(inp, training, enc_padding_mask)
        
        dec_output = self.decode(tar, enc_output, training, look_ahead_mask, dec_padding_mask)

        output = self.output_layer.forward_pass(dec_output)
        
        return output

    def backward_pass(self, error: np.ndarray) -> None:
        error = clip_gradients(error)
        
        dx = self.output_layer.backward_pass(error)
        d_enc_output = None
        
        for decoder_layer in reversed(self.decoder_layers):
            dx, d_enc = decoder_layer.backward_pass(dx)
            d_enc_output = d_enc if d_enc_output is None else d_enc_output + d_enc
                    
        dx = self.decoder_dropout.backward_pass(dx)
        dx = self.tgt_embedding.backward_pass(dx)
        dx = self.positional_encoding.backward_pass(dx)
        
        dx_enc = d_enc_output
        for encoder_layer in reversed(self.encoder_layers):
            dx_enc = encoder_layer.backward_pass(dx_enc)
        
        dx_enc = self.encoder_dropout.backward_pass(dx_enc)
        dx_enc = self.src_embedding.backward_pass(dx_enc)

    def prepare_data(self, x_train: np.ndarray, y_train: np.ndarray, normalize: bool = False) -> tuple[np.ndarray, np.ndarray]:
        max_seq_len = self.max_sequence_length
        
        x_train_with_tokens = [
            [self.SRC_SOS_IDX] + seq + [self.SRC_EOS_IDX] for seq in x_train
        ]
        x_train_padded = pad_sequences(x_train_with_tokens, 
                                    max_length=max_seq_len,
                                    padding='post', 
                                    pad_value=self.PAD_IDX)
        
        y_train_with_tokens = [
            [self.TGT_SOS_IDX] + seq + [self.TGT_EOS_IDX] for seq in y_train
        ]
        y_train_padded = pad_sequences(y_train_with_tokens, 
                                    max_length=max_seq_len,
                                    padding='post', 
                                    pad_value=self.PAD_IDX)
        
        if normalize:
            x_mean = np.mean(x_train_padded)
            x_std = np.std(x_train_padded) + 1e-8
            y_mean = np.mean(y_train_padded)
            y_std = np.std(y_train_padded) + 1e-8

            x_train_normalized = (x_train_padded - x_mean) / x_std
            y_train_normalized = (y_train_padded - y_mean) / y_std

            return x_train_normalized, y_train_normalized
        
        return x_train_padded, y_train_padded

    def compile(self, 
                loss_function: LossFunction | str, 
                optimizer: Optimizer | str, 
                verbose: bool = False) -> None:
        self.loss_function = loss_function if isinstance(loss_function, LossFunction) else LossFunction.from_name(loss_function)
        self.optimizer = optimizer if isinstance(optimizer, Optimizer) else Optimizer.from_name(optimizer)
        
        if isinstance(self.loss_function, SequenceCrossEntropy):
            special_tokens = [self.PAD_IDX, self.TGT_UNK_IDX, self.TGT_SOS_IDX, self.TGT_EOS_IDX]
            self.loss_function.ignore_tokens = list(set(self.loss_function.ignore_tokens + special_tokens))
        
        if verbose:
            print(str(self))

    def train_on_batch(self, x_batch: np.ndarray, y_batch: np.ndarray, print_logging: bool = False) -> float:
        self.gradient_norms = {}
        
        decoder_input = y_batch[:, :-1]
        targets = y_batch[:, 1:]
        
        self.enc_seq_length = x_batch.shape[1]  
        self.dec_seq_length = decoder_input.shape[1]
        
        self.predictions = self.forward_pass((x_batch, decoder_input), training=True)
        
        loss = self.loss_function(targets, self.predictions)
        
        error = self.loss_function.derivative(targets, self.predictions)
        self.backward_pass(error)
        
        def update_with_monitoring(name: str, layer_idx: int, weights: np.ndarray, 
                                 d_weights: np.ndarray, bias: np.ndarray, d_bias: np.ndarray) -> None:
            weight_norm = np.linalg.norm(d_weights)
            bias_norm = np.linalg.norm(d_bias) if d_bias is not None else 0
            
            self.gradient_norms[name] = {
                'weights': float(weight_norm),
                'bias': float(bias_norm)
            }
            
            if print_logging and weight_norm > self.gradient_clip_threshold:
                logging.warning(f"Large gradient norm in {name}: {weight_norm:.4f}")
            
            self.optimizer.update(layer_idx, weights, d_weights, bias, d_bias)
        
        layer_idx = 0
        
        update_with_monitoring(
            'src_embedding', layer_idx,
            self.src_embedding.weights, self.src_embedding.d_weights,
            self.src_embedding.bias, self.src_embedding.d_bias
        )
        layer_idx += 1
        
        update_with_monitoring(
            'tgt_embedding', layer_idx,
            self.tgt_embedding.weights, self.tgt_embedding.d_weights,
            self.tgt_embedding.bias, self.tgt_embedding.d_bias
        )
        layer_idx += 1
        
        for i, encoder_layer in enumerate(self.encoder_layers):
            update_with_monitoring(
                f'encoder_{i}_self_attn_Q', layer_idx,
                encoder_layer.attention.query_dense.weights,
                encoder_layer.attention.query_dense.d_weights,
                encoder_layer.attention.query_dense.bias,
                encoder_layer.attention.query_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'encoder_{i}_self_attn_K', layer_idx,
                encoder_layer.attention.key_dense.weights,
                encoder_layer.attention.key_dense.d_weights,
                encoder_layer.attention.key_dense.bias,
                encoder_layer.attention.key_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'encoder_{i}_self_attn_V', layer_idx,
                encoder_layer.attention.value_dense.weights,
                encoder_layer.attention.value_dense.d_weights,
                encoder_layer.attention.value_dense.bias,
                encoder_layer.attention.value_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'encoder_{i}_self_attn_O', layer_idx,
                encoder_layer.attention.output_dense.weights,
                encoder_layer.attention.output_dense.d_weights,
                encoder_layer.attention.output_dense.bias,
                encoder_layer.attention.output_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'encoder_{i}_ffn_1', layer_idx,
                encoder_layer.ffn.dense1.weights,
                encoder_layer.ffn.dense1.d_weights,
                encoder_layer.ffn.dense1.bias,
                encoder_layer.ffn.dense1.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'encoder_{i}_ffn_2', layer_idx,
                encoder_layer.ffn.dense2.weights,
                encoder_layer.ffn.dense2.d_weights,
                encoder_layer.ffn.dense2.bias,
                encoder_layer.ffn.dense2.d_bias
            )
            layer_idx += 1
        
        for i, decoder_layer in enumerate(self.decoder_layers):
            update_with_monitoring(
                f'decoder_{i}_self_attn_Q', layer_idx,
                decoder_layer.self_attention.query_dense.weights,
                decoder_layer.self_attention.query_dense.d_weights,
                decoder_layer.self_attention.query_dense.bias,
                decoder_layer.self_attention.query_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_self_attn_K', layer_idx,
                decoder_layer.self_attention.key_dense.weights,
                decoder_layer.self_attention.key_dense.d_weights,
                decoder_layer.self_attention.key_dense.bias,
                decoder_layer.self_attention.key_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_self_attn_V', layer_idx,
                decoder_layer.self_attention.value_dense.weights,
                decoder_layer.self_attention.value_dense.d_weights,
                decoder_layer.self_attention.value_dense.bias,
                decoder_layer.self_attention.value_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_self_attn_O', layer_idx,
                decoder_layer.self_attention.output_dense.weights,
                decoder_layer.self_attention.output_dense.d_weights,
                decoder_layer.self_attention.output_dense.bias,
                decoder_layer.self_attention.output_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_cross_attn_Q', layer_idx,
                decoder_layer.cross_attention.query_dense.weights,
                decoder_layer.cross_attention.query_dense.d_weights,
                decoder_layer.cross_attention.query_dense.bias,
                decoder_layer.cross_attention.query_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_cross_attn_K', layer_idx,
                decoder_layer.cross_attention.key_dense.weights,
                decoder_layer.cross_attention.key_dense.d_weights,
                decoder_layer.cross_attention.key_dense.bias,
                decoder_layer.cross_attention.key_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_cross_attn_V', layer_idx,
                decoder_layer.cross_attention.value_dense.weights,
                decoder_layer.cross_attention.value_dense.d_weights,
                decoder_layer.cross_attention.value_dense.bias,
                decoder_layer.cross_attention.value_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_cross_attn_O', layer_idx,
                decoder_layer.cross_attention.output_dense.weights,
                decoder_layer.cross_attention.output_dense.d_weights,
                decoder_layer.cross_attention.output_dense.bias,
                decoder_layer.cross_attention.output_dense.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_ffn_1', layer_idx,
                decoder_layer.ffn.dense1.weights,
                decoder_layer.ffn.dense1.d_weights,
                decoder_layer.ffn.dense1.bias,
                decoder_layer.ffn.dense1.d_bias
            )
            layer_idx += 1
            
            update_with_monitoring(
                f'decoder_{i}_ffn_2', layer_idx,
                decoder_layer.ffn.dense2.weights,
                decoder_layer.ffn.dense2.d_weights,
                decoder_layer.ffn.dense2.bias,
                decoder_layer.ffn.dense2.d_bias
            )
            layer_idx += 1
        
        update_with_monitoring(
            'output', layer_idx,
            self.output_layer.weights,
            self.output_layer.d_weights,
            self.output_layer.bias,
            self.output_layer.d_bias
        )
        
        return loss

    def fit(self, x_train: np.ndarray, y_train: np.ndarray,
                epochs: int,
                batch_size: int | None = None,
                verbose: bool = True,
                metrics: list | None = None,
                random_state: int | None = None,
                validation_data: tuple | None = None,
                callbacks: list = []) -> dict:

            history = History({
                'loss': [],
                'val_loss': []
            })

            x_train = np.array(x_train) if not isinstance(x_train, np.ndarray) else x_train
            y_train = np.array(y_train) if not isinstance(y_train, np.ndarray) else y_train

            if metrics is not None:
                metrics: list[Metric] = [Metric(m) for m in metrics]
                for metric in metrics:
                    history[metric.name] = []
                    history[f'val_{metric.name}'] = []

            if callbacks is None:
                callbacks = []

            for callback in callbacks:
                callback.on_train_begin()

            for epoch in range(epochs):
                for callback in callbacks:
                    callback.on_epoch_begin(epoch)

                start_time = time.time()
                x_train_shuffled, y_train_shuffled = shuffle(x_train, y_train,
                                                        random_state=random_state if random_state is not None else self.random_state)
                error = 0
                predictions_list = []
                y_true_list = []

                if batch_size is not None:
                    num_batches = np.ceil(x_train.shape[0] / batch_size).astype(int)
                    for j in range(0, x_train.shape[0], batch_size):
                        x_batch = x_train_shuffled[j:j + batch_size]
                        y_batch = y_train_shuffled[j:j + batch_size]

                        error += self.train_on_batch(x_batch, y_batch)
                        predictions_list.append(self.predictions)
                        y_true_list.append(y_batch[:, 1:])  # Remove SOS token

                        if verbose:
                            metrics_str = ''
                            if metrics is not None:
                                for metric in metrics:
                                    metric_value = metric(np.vstack(predictions_list), np.vstack(y_true_list))
                                    metrics_str += f'{metric.name}: {format_number(metric_value)} - '
                            progress_bar(j / batch_size + 1, num_batches,
                                    message=f'Epoch {epoch + 1}/{epochs} - loss: {format_number(error / (j / batch_size + 1))} - {metrics_str[:-3]} - {time.time() - start_time:.2f}s')

                    error /= num_batches
                else:
                    error = self.train_on_batch(x_train, y_train)
                    predictions_list.append(self.predictions)
                    y_true_list.append(y_train[:, 1:])  # Remove SOS token

                    if verbose:
                        metrics_str = ''
                        if metrics is not None:
                            for metric in metrics:
                                metric_value = metric(np.vstack(predictions_list), np.vstack(y_true_list))
                                history[metric.name].append(metric_value)
                                metrics_str += f'{metric.name}: {format_number(metric_value)} - '
                        progress_bar(1, 1,
                                message=f'Epoch {epoch + 1}/{epochs} - loss: {format_number(error)} - {metrics_str[:-3]} - {time.time() - start_time:.2f}s')

                history['loss'].append(error)

                logs = {'loss': error}
                if metrics is not None:
                    for metric in metrics:
                        metric_value = metric(np.vstack(predictions_list), np.vstack(y_true_list))
                        logs[metric.name] = metric_value

                if validation_data is not None:
                    x_test, y_test = validation_data
                    val_loss, val_predictions = self.evaluate(x_test, y_test, batch_size)
                    history['val_loss'].append(val_loss)
                    logs['val_loss'] = val_loss

                    if metrics is not None:
                        val_metrics = []
                        for metric in metrics:
                            val_metric = metric(val_predictions, y_test[:, 1:])  # Remove SOS token
                            history[f'val_{metric.name}'].append(val_metric)
                            logs[f'val_{metric.name}'] = val_metric
                            val_metrics.append(val_metric)
                        if verbose:
                            val_metrics_str = ' - '.join(
                                f'val_{metric.name}: {format_number(val_metric)}'
                                for metric, val_metric in zip(metrics, val_metrics)
                            )
                            print(f' - {val_metrics_str}', end='')

                    val_predictions = None

                stop_training = False
                for callback in callbacks:
                    if isinstance(callback, EarlyStopping):
                        if callback.on_epoch_end(epoch, {**logs, 'model': self}):
                            stop_training = True
                            break
                    else:
                        callback.on_epoch_end(epoch, logs)

                if verbose:
                    print()

                if stop_training:
                    break

            for callback in callbacks:
                callback.on_train_end()

            if verbose:
                print()

            return history
        
    def predict(self, inp: np.ndarray, max_length: int = 50, beam_size: int = 5, 
                alpha: float = 0.6, min_length: int = 5, temperature: float = 1.0) -> np.ndarray:
        batch_size = inp.shape[0]
        enc_output = self.encode(inp, training=False)
        
        dec_input = np.full((batch_size, 1), self.TGT_SOS_IDX)
        
        max_output_length = min(max_length, self.max_sequence_length)
        
        outputs = []
        for i in range(max_output_length - 1):
            if dec_input.shape[1] >= self.max_sequence_length:
                break
                
            predictions = self.forward_pass((inp, dec_input), training=False)
            predictions = predictions[:, -1:, :]
            
            scaled_logits = predictions[0, 0] / temperature
            probs = np.exp(scaled_logits - np.max(scaled_logits))
            probs = probs / np.sum(probs)
            
            rng = np.random.default_rng(self.random_state)
            next_token = rng.choice(len(probs), p=probs)
            
            if next_token == self.TGT_EOS_IDX or len(outputs) >= max_output_length - 1:
                break
                
            outputs.append(next_token)
            dec_input = np.array([[self.TGT_SOS_IDX] + outputs])
        
        final_sequence = np.array([[self.TGT_SOS_IDX] + outputs + [self.TGT_EOS_IDX]])
        return final_sequence

    def evaluate(self, 
                x_test: np.ndarray, 
                y_test: np.ndarray, 
                batch_size: int = 32) -> tuple[float, np.ndarray]:
        total_loss = 0
        num_batches = int(np.ceil(len(x_test) / batch_size))
        predictions_list: list = []
        
        for i in range(0, len(x_test), batch_size):
            batch_x = x_test[i:i + batch_size]
            batch_y = y_test[i:i + batch_size]
            
            predictions = self.forward_pass((batch_x, batch_y[:, :-1]), training=False)
            batch_loss = self.loss_function(batch_y[:, 1:], predictions)
            
            total_loss += batch_loss
            predictions_list.append(predictions)
            
        avg_loss = total_loss / num_batches
        all_predictions = np.vstack(predictions_list)
        
        return avg_loss, all_predictions

    def get_config(self) -> dict:
        return {
            'src_vocab_size': self.src_vocab_size,
            'tgt_vocab_size': self.tgt_vocab_size,
            'd_model': self.d_model,
            'n_heads': self.n_heads,
            'n_encoder_layers': self.n_encoder_layers,
            'n_decoder_layers': self.n_decoder_layers,
            'd_ff': self.d_ff,
            'dropout_rate': self.dropout_rate,
            'max_sequence_length': self.max_sequence_length,
            'gradient_clip_threshold': self.gradient_clip_threshold,
            'enable_padding': self.enable_padding,
            'padding_size': self.padding_size,
            'random_state': self.random_state
        }

    def save(self, filename: str) -> None:
        config = self.get_config()
        config['type'] = 'Transformer'
        
        with open(filename, 'w') as f:
            json.dump(config, f, indent=4)

    @classmethod
    def load(cls, filename: str) -> 'Transformer':
        with open(filename, 'r') as f:
            config = json.load(f)
            
        if config['type'] != 'Transformer':
            raise ValueError(f"Invalid model type {config['type']}")
            
        return cls(**{k: v for k, v in config.items() if k != 'type'})

    def __str__(self) -> str:
        return (f"Transformer(\n"
                f"  src_vocab_size={self.src_vocab_size},\n"
                f"  tgt_vocab_size={self.tgt_vocab_size},\n"
                f"  d_model={self.d_model},\n"
                f"  n_heads={self.n_heads},\n"
                f"  n_encoder_layers={self.n_encoder_layers},\n"
                f"  n_decoder_layers={self.n_decoder_layers},\n"
                f"  d_ff={self.d_ff},\n"
                f"  dropout_rate={self.dropout_rate},\n"
                f"  max_sequence_length={self.max_sequence_length}\n"
                f")")
