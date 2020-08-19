from __future__ import print_function, division, absolute_import
import torch
import torch.nn as nn
import torch.quantization
from torch.quantization.boiler_code import evaluate, imagenet_download, load_conv

import _equalize
import _correct_bias
import _adaround

import copy
# Specify random seed for repeatable results
torch.manual_seed(191009)

def quantize_model(model, data_loader_test, per_tensor=True):
    print("starting quantization")
    criterion = nn.CrossEntropyLoss()
    # num_calibration_batches = 30
    num_eval_batches = 10

    model = copy.deepcopy(model)
    if per_tensor:
        model.qconfig = torch.quantization.default_qconfig
    else:
        model.qconfig = torch.quantization.get_default_qconfig('fbgemm')

    model = torch.quantization.prepare(model, inplace=False)
    evaluate(model, criterion, data_loader_test, neval_batches=num_eval_batches)
    model = torch.quantization.convert(model, inplace=False)

    print("ending quantization")
    return model

def quantize_qat_model(model, data_loader_test):
    print("starting quantization")
    criterion = nn.CrossEntropyLoss()
    # num_calibration_batches = 30
    num_eval_batches = 10

    model = copy.deepcopy(model)
    model.qconfig = torch.quantization.default_qat_qconfig

    model = torch.quantization.prepare_qat(model, inplace=False)
    evaluate(model, criterion, data_loader_test, neval_batches=num_eval_batches)
    model = torch.quantization.convert(model, inplace=False)

    print("ending quantization")
    return model

def adaround_demo(input_model, data_loader, data_loader_test):
    print("starting adaround")
    # train_batch_size = 30
    eval_batch_size = 30
    num_eval_batches = 10
    criterion = nn.CrossEntropyLoss()
    model = copy.deepcopy(input_model)

    # throwing on the equalization
    model.eval()
    model.fuse_model()
    print(model)

    quantized_qat_model = quantize_qat_model(model, data_loader)
    results = []

    # top1, top5 = evaluate(model, criterion, data_loader_test, neval_batches=num_eval_batches)
    # results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    # results.append('Floating point results')

    top1, top5 = evaluate(quantized_qat_model, criterion, data_loader_test, neval_batches=num_eval_batches)
    results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    results.append('qat quantization accuracy results, no adaround')

    # model.qconfig = _adaround.adaround_qconfig
    print("starting adaround prep")
    names = _adaround.prepare_adaround(model, data_loader)
    # also does the training, so will need to rename this
    with_adaround = copy.deepcopy(model)
    without_adaround = copy.deepcopy(with_adaround)
    print("finished adaround prep")

    top1, top5 = evaluate(without_adaround, criterion, data_loader_test, neval_batches=num_eval_batches)
    results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    results.append('just qat accuracy results')
    results.append('')
    print(results)

    names = []
    count = 0
    for name, submodule in with_adaround.named_modules():
        if isinstance(submodule, _adaround.OutputWrapper):
            names.append(name)
            count += 1
            if count == 3:
                break

    for name in names:
        print(names)
        _adaround.learn_adaround(with_adaround, data_loader, name)

        top1, top5 = evaluate(with_adaround, criterion, data_loader_test, neval_batches=num_eval_batches)
        results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
        results.append(name)
        results.append('with adaround accuracy results')
        print(results)



    # _adaround.learn_adaround_parallel(model, data_loader_test)

    # top1, top5 = evaluate(model, criterion, data_loader_test, neval_batches=num_eval_batches)
    # results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    # results.append('with adaround PARALLEL accuracy results')


    print("\n\n Results reiterated here")
    for result in results:
        print(result)

def correct_bias_demo(input_model, data_loader, data_loader_test):
    eval_batch_size = 30
    num_eval_batches = 3 * 10
    num_calibration_batches = 10
    criterion = nn.CrossEntropyLoss()
    model = copy.deepcopy(input_model)

    # throwing on the equalization
    model.eval()
    model.fuse_model()
    input_revised = grab_names(model)
    _equalize.equalize(model, input_revised, 1e-4)
    results = []


    quantized_tensor_model = quantize_model(model, data_loader, True)
    top1, top5 = evaluate(quantized_tensor_model, criterion, data_loader_test, neval_batches=num_eval_batches)
    results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    results.append('Per tensor quantization accuracy results, no bias correction')

    quantized_channel_model = quantize_model(model, data_loader, False)
    top1, top5 = evaluate(quantized_channel_model, criterion, data_loader_test, neval_batches=num_eval_batches)
    results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    results.append('Per channel quantization accuracy results, no bias correction')

    _correct_bias.bias_correction(model, quantized_tensor_model, data_loader_test, neval_batches=num_eval_batches)
    top1, top5 = evaluate(quantized_tensor_model, criterion, data_loader_test, neval_batches=num_eval_batches)
    results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    results.append('Per tensor quantization accuracy results, with bias correction')

    _correct_bias.bias_correction(model, quantized_channel_model, data_loader_test, neval_batches=num_eval_batches)
    top1, top5 = evaluate(quantized_channel_model, criterion, data_loader_test, neval_batches=num_eval_batches)
    results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))
    results.append('Per channel quantization accuracy results, with bias correction')

    print("\n\n Results reiterated here")
    for result in results:
        print(result)

def grab_names(model):
    ''' Helper method to find pairs of submodule in mobilenet to apply equalization
    '''
    input = []
    curr_feature = []
    for name, module in model.named_modules():
        if isinstance(module, nn.Conv2d):
            curr_feature.append(name)
        if isinstance(module, nn.quantized.modules.functional_modules.FloatFunctional):
            curr_feature = curr_feature[1:]
            input.append(curr_feature)
            curr_feature = []
    input_revised = []
    for feature in input:
        for i in range(len(feature) - 1):
            input_revised.append([feature[i], feature[i + 1]])
    return input_revised


def equalize_accuracy_demo(input_model, data_loader, data_loader_test):
    eval_batch_size = 30
    num_eval_batches = 10
    num_calibration_batches = 10
    criterion = nn.CrossEntropyLoss()

    results = []

    def eval(per_tensor=True, equalize=False):
        model = copy.deepcopy(input_model)
        input_revised = grab_names(model)
        model.eval()
        model.fuse_model()
        if equalize:
            input_revised = grab_names(model)
            _equalize.equalize(model, input_revised, 1e-4)

        model = quantize_model(model, data_loader, per_tensor)

        for name, module in model.named_modules():
            if hasattr(module, 'qconfig'):
                del module.qconfig

        top1, top5 = evaluate(model, criterion, data_loader_test, neval_batches=num_eval_batches)
        results.append(str('Evaluation accuracy on %d images, %2.2f' % (num_eval_batches * eval_batch_size, top1.avg)))


    eval(per_tensor=True, equalize=False)
    results.append("per tensor, without equalize\n")

    eval(per_tensor=False, equalize=False)
    results.append("per channel, without equalize\n")

    eval(per_tensor=True, equalize=True)
    results.append("per tensor, with equalize\n")

    eval(per_tensor=False, equalize=True)
    results.append("per channel, with equalize\n")

    results.append("is it christmas :O")

    for result in results:
        print(result)


if __name__ == "__main__":
    # equalize_accuracy_demo(*imagenet_download())
    # correct_bias_demo(*imagenet_download())
    adaround_demo(*imagenet_download())
    # _adaround.quantize_adaround(*load_conv())
