import numpy as np


class LossFunction:
    EPSILON = 1e-15

    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        raise NotImplementedError

    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def get_config(self) -> dict:
        return {"name": self.__class__.__name__}

    @staticmethod
    def from_config(config: dict) -> 'LossFunction':
        if config['name'] == 'MeanSquaredError':
            return MeanSquaredError()
        elif config['name'] == 'BinaryCrossentropy':
            return BinaryCrossentropy()
        elif config['name'] == 'CategoricalCrossentropy':
            return CategoricalCrossentropy()
        elif config['name'] == 'MeanAbsoluteError':
            return MeanAbsoluteError()
        elif config['name'] == 'HuberLoss':
            return HuberLoss(config['delta'])
        elif config['name'] == 'KullbackLeiblerDivergence':
            return KullbackLeiblerDivergence()
        else:
            raise ValueError(f'Unknown loss function: {config["name"]}')

    @staticmethod
    def from_name(name: str) -> "LossFunction":
        name = name.lower().replace("_", "")
        if name == "mse" or name == "meansquarederror":
            return MeanSquaredError()
        elif name == "bce" or name == "binarycrossentropy":
            return BinaryCrossentropy()
        elif name == "cce" or name == "categorycrossentropy":
            return CategoricalCrossentropy()
        elif name == "scce" or name == "sparsecategoricalcrossentropy":
            return SparseCategoricalCrossentropy()
        elif name == "mae" or name == "meanabsoluteerror":
            return MeanAbsoluteError()
        elif name == "kld" or name == "kullbackleiblerdivergence":
            return KullbackLeiblerDivergence()
        elif name == "sequencecrossentropy" or name == "sce":
            return SequenceCrossEntropy()
        elif name.startswith("huber") and len(name.split("_")) == 2:
            delta = float(name.split("_")[-1])
            return HuberLoss(delta)
        else:
            for subclass in LossFunction.__subclasses__():
                if subclass.__name__.lower() == name:
                    return subclass()

        raise ValueError(f"No loss function found for the name: {name}")


class MeanSquaredError(LossFunction):
    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        return np.mean(np.square(y_true - y_pred))

    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        return 2 * (y_pred - y_true) / y_true.shape[0]

    def __str__(self):
        return "MeanSquaredError"


class BinaryCrossentropy(LossFunction):
    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        y_pred = np.clip(y_pred, LossFunction.EPSILON, 1 - LossFunction.EPSILON)
        return -np.mean(y_true * np.log(y_pred) + (1 - y_true) * np.log(1 - y_pred))

    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        y_pred = np.clip(y_pred, LossFunction.EPSILON, 1 - LossFunction.EPSILON)
        return (y_pred - y_true) / (y_pred * (1 - y_pred))

    def __str__(self):
        return "BinaryCrossentropy"


class CategoricalCrossentropy(LossFunction):
    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        y_pred = np.clip(y_pred, LossFunction.EPSILON, 1 - LossFunction.EPSILON)
        return -np.sum(y_true * np.log(y_pred)) / y_true.shape[0]

    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        try:
            y_pred = np.clip(y_pred, LossFunction.EPSILON, 1 - LossFunction.EPSILON)
            return -y_true / y_pred
        except Exception as e:
            print(e, "Make sure to one-hot encode your labels.", sep="\n")

    def __str__(self):
        return "CategoricalCrossentropy"


class SparseCategoricalCrossentropy(LossFunction):
    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        y_pred = np.clip(y_pred, LossFunction.EPSILON, 1 - LossFunction.EPSILON)

        batch_size = y_true.shape[0]
        y_pred_selected = y_pred[np.arange(batch_size), y_true]

        return -np.mean(np.log(y_pred_selected))

    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        y_pred = np.clip(y_pred, LossFunction.EPSILON, 1 - LossFunction.EPSILON)

        batch_size = y_true.shape[0]
        y_true_one_hot = np.zeros_like(y_pred)
        y_true_one_hot[np.arange(batch_size), y_true] = 1

        return -y_true_one_hot / y_pred

    def __str__(self):
        return "SparseCategoricalCrossentropy"


class MeanAbsoluteError(LossFunction):
    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        return np.mean(np.abs(y_true - y_pred))

    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        return np.where(y_pred > y_true, 1, -1)

    def __str__(self):
        return "MeanAbsoluteError"


class HuberLoss(LossFunction):
    def __init__(self, delta: float = 1.0):
        super().__init__()
        self.delta = delta

    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        error = y_true - y_pred
        is_small_error = np.abs(error) <= self.delta
        squared_loss = 0.5 * np.square(error)
        linear_loss = self.delta * (np.abs(error) - 0.5 * self.delta)
        return np.mean(np.where(is_small_error, squared_loss, linear_loss))

    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        error = y_true - y_pred
        return np.where(np.abs(error) <= self.delta, error, self.delta * np.sign(error))

    def __str__(self):
        return f"HuberLoss(delta={self.delta})"

    def get_config(self) -> dict:
        return {"name": self.__class__.__name__, "delta": self.delta}


class KullbackLeiblerDivergence(LossFunction):
    def __call__(self, mu: np.ndarray, log_var: np.ndarray) -> float:
        return -0.5 * np.mean(1 + log_var - np.square(mu) - np.exp(log_var))

    def derivative(self, mu: np.ndarray, log_var: np.ndarray) -> tuple:
        d_mu = mu 
        d_log_var = 0.5 * (np.exp(log_var) - 1)
        return d_mu, d_log_var

    def __str__(self):
        return "KullbackLeiblerDivergence"
    

import numpy as np

class SequenceCrossEntropy:
    def __init__(self, label_smoothing: float = 0.1, ignore_tokens: list = None, 
                 repetition_penalty: float = 1.2):
        self.label_smoothing = label_smoothing
        self.epsilon = 1e-10
        self.ignore_tokens = list(ignore_tokens) if ignore_tokens is not None else []
        self.repetition_penalty = repetition_penalty

    def stable_softmax(self, logits: np.ndarray) -> np.ndarray:
        max_logits = np.max(logits, axis=-1, keepdims=True)
        exp_shifted = np.exp(logits - max_logits)
        return exp_shifted / np.sum(exp_shifted, axis=-1, keepdims=True)

    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        y_true = np.asarray(y_true, dtype=np.int32)
        y_pred = np.asarray(y_pred, dtype=np.float32)
        
        if y_true.ndim != 2 or y_pred.ndim != 3 or y_pred.shape[:2] != y_true.shape:
            raise ValueError(f"Shape mismatch: y_true: {y_true.shape}, y_pred: {y_pred.shape}")
        
        y_pred = self.stable_softmax(y_pred)
        
        ignore_tokens = np.array(self.ignore_tokens)
        if ignore_tokens.size > 0:
            is_ignored = np.isin(y_true, ignore_tokens)
            mask = np.logical_not(is_ignored).astype(np.float32)
        else:
            mask = np.ones_like(y_true, dtype=np.float32)
            
        if np.sum(mask) <= self.epsilon:
            return 0.0
            
        y_pred_clipped = np.clip(y_pred, self.epsilon, 1.0 - self.epsilon)
        
        n_classes = y_pred.shape[-1]
        smooth_value = self.label_smoothing / (n_classes - 1)
        smoothed = np.full_like(y_pred, smooth_value, dtype=np.float32)
        
        valid_mask = ((y_true >= 0) & (y_true < n_classes) & mask.astype(bool))
        rows, cols = np.nonzero(valid_mask)
        smoothed[rows, cols] = smooth_value
        smoothed[rows, cols, y_true[valid_mask]] = 1.0 - self.label_smoothing
        
        if not np.isclose(self.repetition_penalty, 1.0, rtol=1e-09, atol=1e-09):
            is_repeated = (y_true[:, 1:] == y_true[:, :-1])
            repeat_mask = mask[:, 1:].astype(bool) & mask[:, :-1].astype(bool) & is_repeated
            if np.any(repeat_mask):
                rows, cols = np.nonzero(repeat_mask)
                smoothed[rows, cols+1] *= self.repetition_penalty
        
        element_loss = -smoothed * np.log(y_pred_clipped + self.epsilon)
        masked_loss = element_loss * mask[..., np.newaxis]
        
        normalizer = np.sum(mask) + self.epsilon
        return np.sum(masked_loss) / normalizer
    
    def derivative(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        y_true = np.asarray(y_true, dtype=np.int32)
        y_pred = np.asarray(y_pred, dtype=np.float32)
        
        if y_true.ndim != 2 or y_pred.ndim != 3 or y_pred.shape[:2] != y_true.shape:
            raise ValueError(f"Shape mismatch: y_true: {y_true.shape}, y_pred: {y_pred.shape}")
        
        y_pred = self.stable_softmax(y_pred)
        
        ignore_tokens = np.array(self.ignore_tokens)
        if ignore_tokens.size > 0:
            is_ignored = np.isin(y_true, ignore_tokens)
            mask = np.logical_not(is_ignored).astype(np.float32)
        else:
            mask = np.ones_like(y_true, dtype=np.float32)
            
        if np.sum(mask) <= self.epsilon:
            return np.zeros_like(y_pred)
            
        y_pred_clipped = np.clip(y_pred, self.epsilon, 1.0 - self.epsilon)
        
        n_classes = y_pred.shape[-1]
        smooth_value = self.label_smoothing / (n_classes - 1)
        smoothed = np.full_like(y_pred, smooth_value, dtype=np.float32)
        
        valid_mask = ((y_true >= 0) & (y_true < n_classes) & mask.astype(bool))
        rows, cols = np.nonzero(valid_mask)
        smoothed[rows, cols] = smooth_value
        smoothed[rows, cols, y_true[valid_mask]] = 1.0 - self.label_smoothing
        
        if not np.isclose(self.repetition_penalty, 1.0, rtol=1e-09, atol=1e-09):
            is_repeated = (y_true[:, 1:] == y_true[:, :-1])
            repeat_mask = mask[:, 1:].astype(bool) & mask[:, :-1].astype(bool) & is_repeated
            if np.any(repeat_mask):
                rows, cols = np.nonzero(repeat_mask)
                smoothed[rows, cols+1] *= self.repetition_penalty
        
        normalizer = (1.0 - self.label_smoothing) + self.label_smoothing / (n_classes - 1)
        grad = -(smoothed / (y_pred_clipped + self.epsilon)) / normalizer
        masked_grad = grad * mask[..., np.newaxis]
        
        normalizer = np.sum(mask) + self.epsilon
        return masked_grad / normalizer
