# coding=utf-8
# Copyright 2018 The Google AI Language Team Authors and The HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Extract pre-computed feature vectors from a PyTorch BERT model."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import collections
import logging
import json
import re
import nltk
nltk.download('punkt')

import torch
from torch.utils.data import TensorDataset, DataLoader, SequentialSampler
from torch.utils.data.distributed import DistributedSampler

from pytorch_pretrained_bert.tokenization import BertTokenizer
from pytorch_pretrained_bert.modeling import BertForPPL

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s', 
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)


class InputExample(object):

    def __init__(self, unique_id, raw_id, text_a, text_b,labels):
        self.unique_id = unique_id
        self.raw_id=raw_id
        self.text_a = text_a
        self.text_b = text_b
        self.labels=labels


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, unique_id, raw_id, target_ids, input_ids, input_mask, input_type_ids):
        self.unique_id = unique_id
        self.raw_id=raw_id
        self.target_ids = target_ids
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.input_type_ids = input_type_ids


def convert_examples_to_features(examples, seq_length, tokenizer):
    """Loads a data file into a list of `InputBatch`s."""

    features = []
    attention_mask = torch.tril(torch.ones(seq_length, seq_length, dtype=torch.long),-1)+torch.triu(torch.ones(seq_length, seq_length, dtype=torch.long),1)

    for (ex_index, example) in enumerate(examples):
        tokens_a = example.text_a
        labels=example.labels

        tokens_b = None
        if example.text_b:
            tokens_b = tokenizer.tokenize(example.text_b)

        if tokens_b:
            # Modifies `tokens_a` and `tokens_b` in place so that the total
            # length is less than the specified length.
            # Account for [CLS], [SEP], [SEP] with "- 3"
            _truncate_seq_pair(tokens_a, tokens_b, seq_length - 3)
        else:
            # Account for [CLS] and [SEP] with "- 2"
            if len(tokens_a) > seq_length - 2:
                tokens_a = tokens_a[0:(seq_length - 2)]
            if len(labels)>seq_length - 2:
                labels = labels[0:(seq_length - 2)]

        # The convention in BERT is:
        # (a) For sequence pairs:
        #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
        #  type_ids:   0   0  0    0    0     0      0   0    1  1  1   1  1   1
        # (b) For single sequences:
        #  tokens:   [CLS] the dog is hairy . [SEP]
        #  type_ids:   0   0   0   0  0     0   0
        #
        # Where "type_ids" are used to indicate whether this is the first
        # sequence or the second sequence. The embedding vectors for `type=0` and
        # `type=1` were learned during pre-training and are added to the wordpiece
        # embedding vector (and position vector). This is not *strictly* necessary
        # since the [SEP] token unambigiously separates the sequences, but it makes
        # it easier for the model to learn the concept of sequences.
        #
        # For classification tasks, the first vector (corresponding to [CLS]) is
        # used as as the "sentence vector". Note that this only makes sense because
        # the entire model is fine-tuned.
        tokens = []
        input_type_ids = []
        targets=[]
        tokens.append("[CLS]")
        targets.append("[PAD]")
        input_type_ids.append(0)
        for i,token in enumerate(tokens_a):
            tokens.append(token)
            input_type_ids.append(0)
            targets.append(labels[i])
        tokens.append("[SEP]")
        targets.append("[PAD]")
        input_type_ids.append(0)

        if tokens_b:
            for token in tokens_b:
                tokens.append(token)
                input_type_ids.append(1)
            tokens.append("[SEP]")
            input_type_ids.append(1)

        input_ids = tokenizer.convert_tokens_to_ids(tokens)
        target_ids= tokenizer.convert_tokens_to_ids(targets)


        # The mask has 1 for real tokens and 0 for padding tokens. Only real
        # tokens are attended to.
        input_mask = [1] * len(input_ids)

        # Zero-pad up to the sequence length.
        while len(input_ids) < seq_length:
            input_ids.append(0)
            input_mask.append(0)
            target_ids.append(0)
            input_type_ids.append(0)

        assert len(input_ids) == seq_length
        assert len(input_mask) == seq_length
        assert len(target_ids) == seq_length
        assert len(input_type_ids) == seq_length
        #input_mask=(torch.tensor(input_mask, dtype=torch.long).unsqueeze(0)*attention_mask).tolist()

        if ex_index < 0:
            logger.info("*** Example ***")
            logger.info("unique_id: %s" % (example.unique_id))
            logger.info("tokens: %s" % " ".join([str(x) for x in tokens]))
            logger.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
            logger.info("target_ids: %s" % " ".join([str(x) for x in target_ids]))
            logger.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
            logger.info(
                "input_type_ids: %s" % " ".join([str(x) for x in input_type_ids]))
        if True:
            logger.info("*** Example ***")
            logger.info("unique_id: %s" % (example.unique_id))
            logger.info("tokens: %s" % " ".join([str(x) for x in labels]))


        features.append(
            InputFeatures(
                unique_id=example.unique_id,
                raw_id=example.raw_id,
                target_ids=target_ids,
                input_ids=input_ids,
                input_mask=input_mask,
                input_type_ids=input_type_ids))
    return features


def _truncate_seq_pair(tokens_a, tokens_b, max_length):
    """Truncates a sequence pair in place to the maximum length."""

    # This is a simple heuristic which will always truncate the longer sequence
    # one token at a time. This makes more sense than truncating an equal percent
    # of tokens from each, since if one sequence is very short then each token
    # that's truncated likely contains more information than a longer sequence.
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_length:
            break
        if len(tokens_a) > len(tokens_b):
            tokens_a.pop()
        else:
            tokens_b.pop()


def read_examples(input_file, abbr_file, freq_file, tokenizer):
    """Read a list of `InputExample`s from an input file."""
    examples = []
    unique_id = 0
    raw_id=0
    dic={}
    freq=set()
    tlist=[]
    alist=[]

    with open(abbr_file,encoding='utf8') as f:
        dic=json.loads(f.read())
    with open(input_file, "r", encoding='utf-8') as reader:
        while True:
            text_b = None
            line = reader.readline()
            if not line:
                break
            text_a=line.split()
            abbr_pos=-1
            abbr=''
            for i,t in enumerate(text_a):
                if t.lower() in dic:
                    print(t)
                    abbr_pos=i
                    abbr=t.lower()
            if abbr_pos==-1:
                continue
            alist.append(abbr)
            left=tokenizer.tokenize(' '.join(text_a[:abbr_pos]))
            right=tokenizer.tokenize(' '.join(text_a[abbr_pos+1:]))
            labels=['[PAD]']*len(left)+tokenizer.tokenize(abbr)+['[PAD]']*len(right)
            #labels = left + tokenizer.tokenize(abbr) + right
            #text=left+['[MASK]']*len(tokenizer.tokenize(abbr))+right
            text=left+tokenizer.tokenize(abbr)+right
            examples.append(
                InputExample(unique_id=unique_id, raw_id=raw_id, text_a=text, text_b=text_b, labels=labels))
            tlist.append(' '.join(text))
            unique_id += 1
            for d in dic[abbr]:
                alist.append(d)
                d=tokenizer.tokenize(d)
                labels = ['[PAD]'] * len(left) + d + ['[PAD]'] * len(right)
                #labels = left + d + right
                #text = left + ['[MASK]'] * len(d) + right
                text=left+d+right
                examples.append(
                    InputExample(unique_id=unique_id, raw_id=raw_id, text_a=text, text_b=text_b, labels=labels))
                tlist.append(' '.join(text))
                unique_id += 1
            raw_id+=1
    return examples, tlist, alist


def main():
    parser = argparse.ArgumentParser()

    ## Required parameters
    parser.add_argument("--input_file", default=None, type=str, required=True)
    parser.add_argument("--output_file", default=None, type=str, required=True)
    parser.add_argument("--abbr_file", default=None, type=str, required=True)
    parser.add_argument("--freq_file", default=None, type=str, required=False)
    parser.add_argument("--bert_model", default=None, type=str, required=True,
                        help="Bert pre-trained model selected in the list: bert-base-uncased, "
                             "bert-large-uncased, bert-base-cased, bert-base-multilingual, bert-base-chinese.")

    ## Other parameters
    parser.add_argument("--do_lower_case", action='store_true', help="Set this flag if you are using an uncased model.")
    parser.add_argument("--layers", default="-1,-2,-3,-4", type=str)
    parser.add_argument("--max_seq_length", default=128, type=int,
                        help="The maximum total input sequence length after WordPiece tokenization. Sequences longer "
                            "than this will be truncated, and sequences shorter than this will be padded.")
    parser.add_argument("--batch_size", default=1, type=int, help="Batch size for predictions.")
    parser.add_argument("--local_rank",
                        type=int,
                        default=-1,
                        help = "local_rank for distributed training on gpus")
    parser.add_argument("--no_cuda",
                        action='store_true',
                        help="Whether not to use CUDA when available")

    args = parser.parse_args()

    if args.local_rank == -1 or args.no_cuda:
        device = torch.device("cuda" if torch.cuda.is_available() and not args.no_cuda else "cpu")
        n_gpu = torch.cuda.device_count()
    else:
        device = torch.device("cuda", args.local_rank)
        n_gpu = 1
        # Initializes the distributed backend which will take care of sychronizing nodes/GPUs
        torch.distributed.init_process_group(backend='nccl')
    logger.info("device: {} n_gpu: {} distributed training: {}".format(device, n_gpu, bool(args.local_rank != -1)))

    layer_indexes = [int(x) for x in args.layers.split(",")]

    tokenizer = BertTokenizer.from_pretrained(args.bert_model, do_lower_case=args.do_lower_case)

    examples, tlist, alist = read_examples(args.input_file, args.abbr_file, args.freq_file, tokenizer)

    features = convert_examples_to_features(
        examples=examples, seq_length=args.max_seq_length, tokenizer=tokenizer)

    unique_id_to_feature = {}
    for feature in features:
        unique_id_to_feature[feature.unique_id] = feature

    model = BertForPPL.from_pretrained(args.bert_model)
    model.to(device)

    if args.local_rank != -1:
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[args.local_rank],
                                                          output_device=args.local_rank)
    elif n_gpu > 1:
        model = torch.nn.DataParallel(model)

    all_input_ids = torch.tensor([f.input_ids for f in features], dtype=torch.long)
    all_input_mask = torch.tensor([f.input_mask for f in features], dtype=torch.long)
    all_target_ids = torch.tensor([f.target_ids for f in features], dtype=torch.long)
    all_example_index = torch.arange(all_input_ids.size(0), dtype=torch.long)

    eval_data = TensorDataset(all_input_ids, all_input_mask, all_target_ids, all_example_index)
    if args.local_rank == -1:
        eval_sampler = SequentialSampler(eval_data)
    else:
        eval_sampler = DistributedSampler(eval_data)
    eval_dataloader = DataLoader(eval_data, sampler=eval_sampler, batch_size=args.batch_size)

    model.eval()
    loss_list=[]
    with open(args.output_file, "w", encoding='utf-8') as writer:
        for input_ids, input_mask, target_ids, example_indices in eval_dataloader:
            input_ids = input_ids.to(device)
            target_ids=target_ids.to(device)
            input_mask = input_mask.to(device)
            with torch.no_grad():
                loss = model(input_ids, token_type_ids=None, masked_lm_labels=target_ids, attention_mask=input_mask)
                print(example_indices,loss)
                loss_list.append(float(loss))
            for b, example_index in enumerate(example_indices):
                feature = features[example_index.item()]
                unique_id = int(feature.unique_id)
                raw_id=int(feature.raw_id)
                # feature = unique_id_to_feature[unique_id]
                output_json = collections.OrderedDict()
                output_json["index"] = unique_id
                output_json['sent_id']=raw_id
                output_json['text']=tlist[unique_id]
                output_json['expansion']=alist[unique_id]
                output_json["loss"] = float(loss)
                writer.write(json.dumps(output_json) + "\n")


if __name__ == "__main__":
    main()