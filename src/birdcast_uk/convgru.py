"""Optional trainable residual used by the process-guided state transition."""

from __future__ import annotations


def build_convgru(input_channels: int, hidden_channels: int = 32):
    """Return a compact ConvGRU residual model when the training extra is installed."""

    try:
        import torch
        from torch import nn
    except ImportError as exc:  # pragma: no cover - optional production dependency
        raise RuntimeError("Install birdcast-uk[training] to train the ConvGRU residual") from exc

    class ConvGRUResidual(nn.Module):
        def __init__(self):
            super().__init__()
            channels = input_channels + hidden_channels
            self.gates = nn.Conv2d(channels, hidden_channels * 2, 3, padding=1)
            self.candidate = nn.Conv2d(channels, hidden_channels, 3, padding=1)
            self.output = nn.Conv2d(hidden_channels, 3, 1)

        def forward(self, inputs, hidden=None):
            if hidden is None:
                hidden = torch.zeros(
                    inputs.shape[0],
                    hidden_channels,
                    inputs.shape[-2],
                    inputs.shape[-1],
                    device=inputs.device,
                    dtype=inputs.dtype,
                )
            reset, update = torch.sigmoid(self.gates(torch.cat([inputs, hidden], dim=1))).chunk(2, dim=1)
            candidate = torch.tanh(self.candidate(torch.cat([inputs, reset * hidden], dim=1)))
            hidden = (1 - update) * hidden + update * candidate
            residual = self.output(hidden)
            return residual, hidden

    return ConvGRUResidual()
