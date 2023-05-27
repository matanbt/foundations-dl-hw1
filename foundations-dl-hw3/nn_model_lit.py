import itertools
from typing import Tuple, List

import numpy as np


import pandas as pd
import torch
import torch.nn as nn

from pytorch_lightning import LightningModule, Trainer, seed_everything
from pytorch_lightning.callbacks import LearningRateMonitor, ModelCheckpoint, EarlyStopping
from lightning.pytorch.loggers import TensorBoardLogger
from pytorch_lightning.callbacks.progress import TQDMProgressBar
from pytorch_lightning.loggers import CSVLogger
from torchmetrics.functional import f1_score


class LitNNModel(LightningModule):
    def __init__(self,
                 # input-output dims
                 n_features: int = 9,
                 n_classes: int = 2,

                 # arch. hyperparameters
                 hidden_layers_dim: int = 50,
                 hidden_layers_count: int = 3,
                 hidden_layers_list: List[int] = None,  # if not None overrides the previous
                 activation_func: torch.nn.Module = torch.nn.ReLU,
                 loss_func: torch.nn.Module = nn.MSELoss,

                 # opt. hyperparameters
                 lr: float = 0.01,
                 weight_decay: float = 0.005,
                 ):
        super().__init__()

        self.save_hyperparameters()
        self.loss = loss_func
        self.activation = activation_func

        # build model:
        layers = []
        if hidden_layers_list is None:
            hidden_layers_list = [hidden_layers_dim] * hidden_layers_count
        prev_output_dim = n_features
        for curr_hidden_dim in hidden_layers_list:  # append hidden layers
            layers += [
                torch.nn.Linear(prev_output_dim, curr_hidden_dim),
                self.activation,
            ]
            prev_output_dim = curr_hidden_dim
        layers += [  # append final layer
            torch.nn.Linear(prev_output_dim, n_classes),
        ]
        self.model = torch.nn.Sequential(*layers)

    def forward(self, x):
        out = self.model(x)
        return out

    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = self.loss(logits, y)
        self.evaluate(batch, logits=logits, stage='train')
        return loss

    def on_before_optimizer_step(self, optimizer):
        """called after loss.backward() and before optimizers are stepped., to inspect weights/gradients/etc."""

        # Gradient magnitude:
        grad_magnitude = 0
        for parameter in self.parameters():
            grad_magnitude += torch.linalg.norm(parameter.grad)  # TODO what is the definition of gradient magnitude?
        self.log(f"grad_magnitude", grad_magnitude, prog_bar=False, on_epoch=True, on_step=False)

        # Hessian values:
        # function from https://github.com/noahgolmant/pytorch-hessian-eigenthings
        from hessian_eigenthings import compute_hessian_eigenthings
        eigenvals, _ = compute_hessian_eigenthings(self.model, self.train_dl, self.loss,
                                                   sum(p.numel() for p in self.model.parameters() if p.requires_grad),
                                                   use_gpu=False)  # TODO verify this works as expected
        self.log(f"max_hessian_eigenval", np.max(eigenvals), prog_bar=False, on_epoch=True, on_step=False)
        self.log(f"min_hessian_eigenval", np.min(eigenvals), prog_bar=False, on_epoch=True, on_step=False)

    def validation_step(self, batch, batch_idx):
        self.evaluate(batch, stage="val")

    def test_step(self, batch, batch_idx):
        self.evaluate(batch, stage="test")

    def evaluate(self, batch, logits=None, stage=None):
        x, y = batch
        if logits is None:
            logits = self(x)
        loss = self.loss(logits, y)

        if stage:
            self.log(f"{stage}_loss", loss, prog_bar=True, on_epoch=True, on_step=False)

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(
            self.parameters(),
            lr=self.hparams.lr,
            weight_decay=self.hparams.weight_decay,
        )
        return {"optimizer": optimizer}


def get_lnn_regression_model(in_dim, out_dim, N=2):
    model = LitNNModel(lr=1e-4,
                       weight_decay=5e-5,
                       n_features=in_dim,
                       n_classes=out_dim,
                       activation_func=torch.nn.Identity(),
                       hidden_layers_list=[50] * N,
                       loss_func=torch.nn.MSELoss()
                       )
    model.to(torch.float64)  # to support the (california houses) regression data

    trainer = Trainer(
        max_epochs=200,
        accelerator="auto",
        devices="auto",
        logger=[CSVLogger(save_dir="logs/", flush_logs_every_n_steps=100),
                TensorBoardLogger("tb_logs", name=f"model-{model._get_name()}")],
        callbacks=[
            TQDMProgressBar(refresh_rate=10),
        ],
    )

    return model, trainer
