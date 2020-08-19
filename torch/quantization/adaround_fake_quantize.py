import torch
import torch.nn.init as init
from torch.quantization.fake_quantize import FakeQuantize
from torch.quantization.observer import HistogramObserver, MovingAverageMinMaxObserver
from torch.quantization.qconfig import *
from torch.quantization.fake_quantize import *


class AdaRoundFakeQuantize(FakeQuantize):
    ''' TODO: what is this class for, what does it do?
    '''
    adaround_specific_attributes = ('continous_V', 'beta_high', 'beta_low', 'norm_scaling', 'regularization_scaling')
    def __init__(self, *args, **keywords):
        for attribute in AdaRoundFakeQuantize.adaround_specific_attributes:
            if attribute in keywords:
                setattr(self, attribute, keywords[attribute])
                del keywords[attribute]
        super(AdaRoundFakeQuantize, self).__init__(*args, **keywords)

    def forward(self, X):
        if self.continous_V is None:
            # Small values for initializing V makes the rounding scheme close to nearest integer
            self.continous_V = torch.nn.Parameter(torch.ones(X.size()) / 10000)
        return super().forward(self.adaround_rounding(X))

    def randomize(self):
        # uniform distribution of vals
        init.kaiming_uniform_(self.continous_V, a=math.sqrt(5))

    @staticmethod
    def clipped_sigmoid(continous_V):
        ''' Function to create a non-vanishing gradient for V

        Paper Reference: https://arxiv.org/pdf/2004.10568.pdf Eq. 23
        '''
        sigmoid_of_V = torch.sigmoid(continous_V)
        scale_and_add = (sigmoid_of_V * 1.2) - 0.1
        return torch.clamp(scale_and_add, 0, 1)

    def adaround_rounding(self, x):
        ''' Using the scale and continous_V parameters of the module, the given tensor x is
        rounded using adaround

        Paper Reference: https://arxiv.org/pdf/2004.10568.pdf Eq. 22
        '''
        weights_divided_by_scale = torch.div(x, self.scale)
        weights_divided_by_scale = torch.floor(weights_divided_by_scale)
        weights_clipped = weights_divided_by_scale + self.clipped_sigmoid(self.continous_V)

        weights_w_adaround_rounding = self.scale * torch.clamp(weights_clipped, self.quant_min, self.quant_max)
        return weights_w_adaround_rounding

    def layer_loss_function(self, count, float_weight, custom_norm=False):
        ''' Calculates the loss function for a submodule
        note: setting custom_norm to true gives the client ability to change the expression
            for norm part in the loss function, by default this expression is the norm of the
            difference between the float_weight given and its adaround rounded counterpart

        Paper Reference: https://arxiv.org/pdf/2004.10568.pdf Eq. 25
        '''
        beta = count / number_of_epochs * (beta_high - beta_low) + beta_low

        if not custom_norm:
            clipped_weight = self.adaround_rounding(float_weight)
            quantized_weight = torch.fake_quantize_per_tensor_affine(clipped_weight, float(self.scale),
                                                                    int(self.zero_point), self.quant_min,
                                                                    self.quant_max)
            Frobenius_norm = torch.norm(float_weight - quantized_weight)  # norm(W - ~W)
        else:
            Frobenius_norm = float_weight  # norm(Wx - ~Wx)

        # calculating regularization factor -> forces values of continous_V to be 0 or 1
        scale = self.scale
        continous_V = self.continous_V
        clip_V = clipped_sigmoid(continous_V)
        spreading_range = torch.abs((2 * clip_V) - 1)
        one_minus_beta = 1 - (spreading_range ** beta)
        regulization = torch.sum(one_minus_beta)

        print("loss function break down: ", Frobenius_norm * norm_scaling, regularization_scaling * regulization)
        # print("sqnr of float and quantized: ", computeSqnr(float_weight, quantized_weight))
        return Frobenius_norm * norm_scaling + regularization_scaling * regulization

default_araround_fake_quant = AdaRoundFakeQuantize.with_args(observer=MovingAverageMinMaxObserver, quant_min=-128, quant_max=127,
                                         dtype=torch.qint8, qscheme=torch.per_tensor_symmetric, reduce_range=False,
                                         beta_high=8, beta_low=2, norm_scaling=10, regularization_scaling=.1, continous_V=None)

adaround_qconfig = QConfig(activation=default_fake_quant,
                           weight=default_araround_fake_quant)
