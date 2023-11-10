from keras.datasets import imdb
from keras_preprocessing.sequence import pad_sequences

from neuralnetlib.activations import Sigmoid, ReLU
from neuralnetlib.layers import Activation, Dense
from neuralnetlib.losses import BinaryCrossentropy
from neuralnetlib.model import Model
from neuralnetlib.optimizers import SGD
from neuralnetlib.metrics import *

from sklearn.datasets import load_breast_cancer
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


def main():
    # 1. Loading a dataset (in this case, IMDB)
    data = load_breast_cancer()
    X, y = data.data, data.target

    # 2. Preprocessing
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    scaler = StandardScaler()
    x_train = scaler.fit_transform(X_train)
    x_test = scaler.transform(X_test)
    y_train = y_train.reshape(-1, 1)
    y_test = y_test.reshape(-1, 1)

    # 3. Model definition
    input_neurons = 30  # Cancer dataset has 30 features
    num_hidden_layers = 2  # Number of hidden layers
    hidden_neurons = 30  # Number of neurons in each hidden layer
    output_neurons = 1  # Binary classification

    model = Model()
    model.add(Dense(input_neurons, hidden_neurons, weights_init='he', random_state=42))
    model.add(Activation(ReLU()))

    for _ in range(num_hidden_layers - 1):
        model.add(Dense(hidden_neurons, hidden_neurons, weights_init='he', random_state=42))
        model.add(Activation(ReLU()))

    model.add(Dense(hidden_neurons, output_neurons, random_state=42))
    model.add(Activation(Sigmoid()))

    # 4. Model compilation
    model.compile(loss_function=BinaryCrossentropy(), optimizer=SGD(learning_rate=0.000001))

    # 5. Model training
    model.train(x_train, y_train, epochs=20, batch_size=128, metrics=[accuracy_score], random_state=42)

    # 6. Model evaluation
    loss = model.evaluate(x_test, y_test)
    print(f'Test loss: {loss}')

    # 7. Model prediction
    y_pred = model.predict(x_test)

    # 8. Printing some metrics
    accuracy = accuracy_score(y_pred, y_test)
    precision = precision_score(y_pred, y_test)
    recall = recall_score(y_pred, y_test)
    f1 = f1_score(y_pred, y_test)

    print(f"Accuracy: {accuracy}")
    print(f"Precision: {precision}")
    print(f"Recall: {recall}")
    print(f"F1 Score: {f1}")
    
    
if __name__ == '__main__':
    main()