# -*- coding: utf-8 -*-
"""llm_optimization (4).ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/16vlMSeeE3qft54X_vmazZyYylsUWPDdd
"""

!pip install mup

import os
import requests
from zipfile import ZipFile
import torch
from torch.utils.data import Dataset, DataLoader
from collections import Counter
import itertools
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torch.utils.tensorboard import SummaryWriter

dataset_url = "https://dldata-public.s3.us-east-2.amazonaws.com/simplebooks.zip"
dataset_dir = "simplebooks"

if not os.path.exists(dataset_dir):
    os.makedirs(dataset_dir, exist_ok=True)
    response = requests.get(dataset_url)
    zip_path = os.path.join(dataset_dir, "simplebooks.zip")
    with open(zip_path, "wb") as f:
        f.write(response.content)
    with ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(dataset_dir)
    os.remove(zip_path)

print("Dataset downloaded and extracted!")

class SimpleBooksTokenizer:
    def __init__(self, vocab_size=30000):
        self.vocab_size = vocab_size
        self.word2idx = {}
        self.idx2word = {}

    def build_vocab(self, texts):
        counter = Counter(itertools.chain.from_iterable(texts))
        most_common = counter.most_common(self.vocab_size - 2)
        self.word2idx = {word: idx + 2 for idx, (word, _) in enumerate(most_common)}
        self.word2idx["<PAD>"] = 0
        self.word2idx["<UNK>"] = 1
        self.idx2word = {idx: word for word, idx in self.word2idx.items()}

    def encode(self, text):
        return [self.word2idx.get(word, self.word2idx["<UNK>"]) for word in text]

    def decode(self, ids):
        return [self.idx2word[idx] for idx in ids]

def load_simplebooks(data_dir, tokenizer, seq_len=60):
    """/content/simplebooks/simplebooks/simplebooks-2"""
    train_path = os.path.join(data_dir, "simplebooks/simplebooks-2/train.txt")
    val_path = os.path.join(data_dir, "simplebooks/simplebooks-2/valid.txt")

    def read_file(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return [line.strip().split() for line in f]

    train_texts = read_file(train_path)
    val_texts = read_file(val_path)

    tokenizer.build_vocab(train_texts)
    train_data = [tokenizer.encode(text) for text in train_texts]
    val_data = [tokenizer.encode(text) for text in val_texts]

    def create_sequences(data):
        sequences = []
        for text in data:
            for i in range(0, len(text) - seq_len + 1, seq_len):
                sequences.append(text[i:i + seq_len])
        return sequences

    train_sequences = create_sequences(train_data)
    val_sequences = create_sequences(val_data)

    return train_sequences, val_sequences, tokenizer


vocab_size = 15000
seq_len=60
tokenizer = SimpleBooksTokenizer(vocab_size=vocab_size)
train_sequences, val_sequences, tokenizer = load_simplebooks(dataset_dir, tokenizer)

print(f"Train Sequences: {len(train_sequences)}")
print(f"Validation Sequences: {len(val_sequences)}")

class SimpleBooksDataset(Dataset):
    def __init__(self, sequences, seq_len, vocab_size):
        self.sequences = sequences
        self.seq_len = seq_len
        self.vocab_size = vocab_size

    def __len__(self):
        return len(self.sequences)

    def __getitem__(self, idx):
        sequence = self.sequences[idx]
        # Ensure indices are clamped to [0, vocab_size-1]
        sequence = torch.tensor(sequence, dtype=torch.long)
        sequence = torch.clamp(sequence, 0, self.vocab_size - 1)
        return sequence

max_token_idx = max(itertools.chain.from_iterable(train_sequences))
print(f"Maximum token index in train_sequences: {max_token_idx}")
assert max_token_idx < vocab_size, "Token indices exceed vocab_size!"

train_dataset = SimpleBooksDataset(train_sequences, seq_len, vocab_size)
train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)

val_dataset = SimpleBooksDataset(train_sequences, seq_len, vocab_size)
val_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)

import time

class GPT2Block(nn.Module):
    def __init__(self, hidden_size, num_heads, ff_hidden_size, dropout=0.1):
        super(GPT2Block, self).__init__()
        self.attn = nn.MultiheadAttention(hidden_size, num_heads, dropout=dropout)
        self.ln1 = nn.LayerNorm(hidden_size)
        self.ff = nn.Sequential(
            nn.Linear(hidden_size, ff_hidden_size),
            nn.GELU(),
            nn.Linear(ff_hidden_size, hidden_size),
            nn.Dropout(dropout)
        )
        self.ln2 = nn.LayerNorm(hidden_size)

    def forward(self, x, attn_mask=None):
        attn_output, _ = self.attn(x, x, x, attn_mask=attn_mask)
        x = self.ln1(x + attn_output)

        ff_output = self.ff(x)
        x = self.ln2(x + ff_output)

        return x


class GPT2Model(nn.Module):
    def __init__(self, vocab_size, max_seq_len, hidden_size, num_heads, num_layers, ff_hidden_size, dropout=0.1):
        super(GPT2Model, self).__init__()
        self.token_emb = nn.Embedding(vocab_size, hidden_size)
        self.pos_emb = nn.Embedding(max_seq_len, hidden_size)
        self.blocks = nn.ModuleList([
            GPT2Block(hidden_size, num_heads, ff_hidden_size, dropout) for _ in range(num_layers)
        ])
        self.ln_f = nn.LayerNorm(hidden_size)
        self.head = nn.Linear(hidden_size, vocab_size)

    def forward(self, x):
        # print("x: ", x.size(), x, x.max())
        seq_len = x.size(1)
        # print("SEQ_LEN: ", seq_len)
        positions = torch.arange(0, seq_len, device=x.device).unsqueeze(0)
        # print("POSITIONS: ", positions.size(), positions)
        emb = self.token_emb(x)
        # print("emb: ",  emb.dtype, emb.size())
        # print("positions", positions.dtype, positions.size())
        pos_emb = self.pos_emb(positions)
        # print("pos_emb: ", pos_emb.size(), pos_emb)
        x = emb + pos_emb
        for block in self.blocks:
            x = block(x)

        x = self.ln_f(x)
        logits = self.head(x)
        return logits


def train(model, train_loader, val_loader, optimizer, criterion, epochs, writer, tag, device='cuda' if torch.cuda.is_available() else 'cpu'):
    model.train()
    global_step = 0
    epoch_times = []
    for epoch in range(epochs):
        epoch_start_time = time.time()
        epoch_loss = 0

        for batch_idx, batch in enumerate(train_loader):
            optimizer.zero_grad()
            batch = batch.to(device)
            logits = model(batch)
            loss = criterion(logits.view(-1, vocab_size), batch.view(-1))
            loss.backward()
            optimizer.step()

            writer.add_scalar(f"{tag}/batch_loss", loss.item(), global_step)
            epoch_loss += loss.item()
            global_step += 1

        epoch_time = time.time() - epoch_start_time
        epoch_times.append(epoch_time)

        epoch_loss /= len(train_loader)
        writer.add_scalar(f"{tag}_train/epoch_loss", epoch_loss, epoch)
        writer.add_scalar(f"{tag}_train/epoch_time", epoch_time, epoch)

        print(f"{tag} - Epoch {epoch + 1}, Loss: {epoch_loss:.4f}, Time: {epoch_time:.2f}s")
        avg_loss, avg_batch_time = validate(model, val_loader, criterion, vocab_size, device)
        writer.add_scalar(f"{tag}_val/epoch_loss", epoch_loss, epoch)
        writer.add_scalar(f"{tag}_val/epoch_time", epoch_time, epoch)
    total_training_time = sum(epoch_times)
    writer.add_text(f"{tag}/total_training_time", f"{total_training_time:.2f}s")

    return epoch_times


def validate(model, dataloader, criterion, vocab_size, device='cuda' if torch.cuda.is_available() else 'cpu'):
    model.eval()
    total_loss = 0
    total_batches = len(dataloader)
    batch_times = []

    with torch.no_grad():
        for batch_idx, batch in enumerate(dataloader):
            batch_start_time = time.time()
            batch = batch.to(device)
            logits = model(batch)
            loss = criterion(logits.view(-1, vocab_size), batch.view(-1))
            total_loss += loss.item()
            batch_times.append(time.time() - batch_start_time)

    avg_loss = total_loss / total_batches
    avg_batch_time = sum(batch_times) / total_batches
    print(f"Validation Loss: {avg_loss:.4f}, Avg Batch Time: {avg_batch_time:.4f}s")

    return avg_loss, avg_batch_time

# vocab_size=11442#max_token_idx-1

hidden_size = 64
ff_hidden_size = 64
num_heads = 4
num_layers = 4
dropout = 0.1
device = 'cuda' if torch.cuda.is_available() else 'cpu'
baseline_model = GPT2Model(vocab_size, seq_len, hidden_size, num_heads, num_layers, ff_hidden_size, dropout).to(device)
optimizer = torch.optim.Adam(baseline_model.parameters(), lr=1e-4)
criterion = nn.CrossEntropyLoss()
writer_baseline = SummaryWriter("runs/baseline")
train(baseline_model, train_loader, val_loader, optimizer, criterion, 10, writer_baseline, "Baseline", device)

hidden_size_target = 1024
ff_hidden_size_target = 512

target_model = GPT2Model(
    vocab_size,
    seq_len,
    hidden_size_target,
    num_heads,
    num_layers,
    ff_hidden_size_target,
    dropout
).cuda()

optimizer_target = torch.optim.Adam(target_model.parameters(), lr=1e-4)
writer_target = SummaryWriter("runs/target")
train(target_model, train_loader, val_loader, optimizer, criterion, 10, writer_baseline, "Target", device)



"""# trying to optimize using mup"""

from mup import MuReadout
from mup import set_base_shapes, MuAdam

class MuGPT2Model(nn.Module):
    def __init__(self, vocab_size, max_seq_len, hidden_size, num_heads, num_layers, ff_hidden_size, dropout=0.1):
        super(MuGPT2Model, self).__init__()
        self.token_emb = nn.Embedding(vocab_size, hidden_size)
        self.pos_emb = nn.Embedding(max_seq_len, hidden_size)
        self.blocks = nn.ModuleList([
            GPT2Block(hidden_size, num_heads, ff_hidden_size, dropout) for _ in range(num_layers)
        ])
        self.ln_f = nn.LayerNorm(hidden_size)
        self.head = MuReadout(hidden_size, vocab_size, readout_zero_init=True)

    def forward(self, x):
        seq_len = x.size(1)
        positions = torch.arange(0, seq_len, device=x.device).unsqueeze(0)
        x = self.token_emb(x) + self.pos_emb(positions)
        for block in self.blocks:
            x = block(x)
        x = self.ln_f(x)
        logits = self.head(x)
        return logits

target_model_for_mup = MuGPT2Model(
    vocab_size,
    seq_len,
    hidden_size_target,
    num_heads,
    num_layers,
    ff_hidden_size_target,
    dropout
).cuda()

set_base_shapes(target_model_for_mup, baseline_model)
optimizer_mup = MuAdam(target_model_for_mup.parameters(), lr=1e-4)
writer_mup = SummaryWriter("runs/mup")
train(target_model_for_mup, train_loader, val_loader, optimizer_mup, criterion, 10, writer_mup, "μP Target")



# Commented out IPython magic to ensure Python compatibility.
# %load_ext tensorboard
# %tensorboard --logdir runs

# !rm -rf runs

def run_train_base(name='baseline',
                  epochs=10,
                  hidden_size = 64,
                  ff_hidden_size = 64,
                  num_heads = 4,
                  num_layers = 4,
                  dropout = 0.1,
                  device = 'cuda' if torch.cuda.is_available() else 'cpu',
                  lr=1e-4,
                  train_loader=train_loader,
                  val_loader=val_loader,
                  seq_len=60):
    full_name = f"{name}.ep{epochs}.hs{hidden_size}.ffhs{ff_hidden_size}.lr{lr}.seqlen{seq_len}"
    baseline_model = GPT2Model(vocab_size, seq_len, hidden_size, num_heads, num_layers, ff_hidden_size, dropout).to(device)
    optimizer = torch.optim.Adam(baseline_model.parameters(), lr=1e-4)
    criterion = nn.CrossEntropyLoss()
    writer_baseline = SummaryWriter(f"runs/{full_name}")
    train(baseline_model, train_loader, val_loader, optimizer, criterion, epochs, writer_baseline, full_name, device)
    return epochs, num_heads, num_layers, dropout, device, lr, baseline_model


def run_train_target(name='target',
                  epochs=10,
                  hidden_size = 64,
                  ff_hidden_size = 64,
                  num_heads = 4,
                  num_layers = 4,
                  dropout = 0.1,
                  device = 'cuda' if torch.cuda.is_available() else 'cpu',
                  lr=1e-4,
                  train_loader=train_loader,
                  val_loader=val_loader):
    full_name = f"{name}.ep{epochs}.hs{hidden_size}.ffhs{ff_hidden_size}.lr{lr}.seqlen{seq_len}"
    baseline_model = GPT2Model(vocab_size, seq_len, hidden_size, num_heads, num_layers, ff_hidden_size, dropout).to(device)
    optimizer = torch.optim.Adam(baseline_model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    writer_baseline = SummaryWriter(f"runs/{full_name}")
    train(baseline_model, train_loader, val_loader, optimizer, criterion, epochs, writer_baseline, full_name, device)
    return target_model


def run_train_mutarget(name='μP Target',
                  epochs=10,
                  hidden_size = 64,
                  ff_hidden_size = 64,
                  num_heads = 4,
                  num_layers = 4,
                  dropout = 0.1,
                  device = 'cuda' if torch.cuda.is_available() else 'cpu',
                  lr=1e-4,
                  baseline_model=None,
                  train_loader=train_loader,
                  val_loader=val_loader):
    full_name = f"{name}.ep{epochs}.hs{hidden_size}.ffhs{ff_hidden_size}.lr{lr}.seqlen{seq_len}"
    target_model_for_mup = MuGPT2Model(vocab_size, seq_len, hidden_size, num_heads, num_layers, ff_hidden_size, dropout).to(device)
    set_base_shapes(target_model_for_mup, baseline_model)
    optimizer = MuAdam(target_model_for_mup.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    writer_baseline = SummaryWriter(f"runs/{full_name}")
    train(target_model_for_mup, train_loader, val_loader, optimizer, criterion, epochs, writer_baseline, full_name, device)
    return target_model_for_mup

# for i in range(1, 5):
#     hidden_size_target = int(256*(2**i))
#     ff_hidden_size_target = int(256*(2**i))
#     epochs, num_heads, num_layers, dropout, device, lr, baseline_model = run_train_base(name='baseline',
#                       epochs=10,
#                       hidden_size = 64,
#                       ff_hidden_size = 64,
#                       num_heads = 4,
#                       num_layers = 4,
#                       dropout = 0.1,
#                       device = 'cuda' if torch.cuda.is_available() else 'cpu',
#                       lr=1e-4)

#     target_model = run_train_target(name='target',
#                       epochs=epochs,
#                       hidden_size = hidden_size_target,
#                       ff_hidden_size = ff_hidden_size_target,
#                       num_heads = num_heads,
#                       num_layers = num_layers,
#                       dropout = dropout,
#                       device = device,
#                       lr=lr)

#     target_model_for_mup = run_train_mutarget(name='μP Target',
#                       epochs=epochs,
#                       hidden_size = hidden_size_target,
#                       ff_hidden_size = ff_hidden_size_target,
#                       num_heads = num_heads,
#                       num_layers = num_layers,
#                       dropout = dropout,
#                       device = device,
#                       lr=lr,
#                       baseline_model=baseline_model)

hidden_size_target = int(256*(2*5))
ff_hidden_size_target = int(256*(2**5))

epochs, num_heads, num_layers, dropout, device, lr, baseline_model = run_train_base(name='baseline',
                  epochs=2,
                  hidden_size = 64,
                  ff_hidden_size = 128,
                  num_heads = 4,
                  num_layers = 4,
                  dropout = 0.1,
                  device = 'cuda' if torch.cuda.is_available() else 'cpu',
                  lr=1e-4)

epochs = 10

target_model_for_mup = run_train_mutarget(name='μP Target',
                  epochs=epochs,
                  hidden_size = hidden_size_target,
                  ff_hidden_size = ff_hidden_size_target,
                  num_heads = num_heads,
                  num_layers = num_layers,
                  dropout = dropout,
                  device = device,
                  lr=1e-4,
                  baseline_model=baseline_model)

target_model = run_train_target(name='target',
                epochs=10,
                hidden_size = hidden_size_target,
                ff_hidden_size = ff_hidden_size_target,
                num_heads = 4,
                num_layers = 4,
                dropout = 0.1,
                device = 'cuda' if torch.cuda.is_available() else 'cpu',
                lr=1e-4)

torch.cuda.empty_cache()



# !zip -r /content/file.zip /content

# hidden_size_target = int(256*(2*5))
# ff_hidden_size_target = int(256*(2**5))
# epochs, num_heads, num_layers, dropout, device, lr, baseline_model = run_train_base(name='baseline',
#                   epochs=10,
#                   hidden_size = 128,
#                   ff_hidden_size = 128,
#                   num_heads = 4,
#                   num_layers = 4,
#                   dropout = 0.1,
#                   device = 'cuda' if torch.cuda.is_available() else 'cpu',
#                   lr=1e-4)

# target_model_for_mup = run_train_mutarget(name='μP Target',
#                   epochs=epochs,
#                   hidden_size = hidden_size_target,
#                   ff_hidden_size = ff_hidden_size_target,
#                   num_heads = num_heads,
#                   num_layers = num_layers,
#                   dropout = dropout,
#                   device = device,
#                   lr=1e-4,
#                   baseline_model=baseline_model)

# from numba import cuda
# device = cuda.get_current_device()
# device.reset()





optimizer_mup = writer_mup = SummaryWriter("runs/mup")
train(target_model_for_mup, train_loader, val_loader, optimizer_mup, criterion, 10, writer_mup, "μP Target")

import os
import pandas as pd
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

def extract_scalars_to_dataframe(event_file):
    event_acc = EventAccumulator(event_file)
    event_acc.Reload()

    scalar_data = {}
    for tag in event_acc.Tags()['scalars']:
        events = event_acc.Scalars(tag)
        steps = [event.step for event in events]
        values = [event.value for event in events]
        if '/' in tag:
            model, metric = tag.split('/', 1)
        else:
            model, metric = tag, "unknown_metric"
        if model not in scalar_data:
            scalar_data[model] = {'step': steps}

        scalar_data[model][metric] = values

    dfs = []
    for model, data in scalar_data.items():
        # model, split = model.split('_')
        df = pd.DataFrame(data)
        df['model'] = model
        # df['split'] = split
        dfs.append(df)
        # print(dfs)

    combined_df = pd.concat(dfs, ignore_index=True)
    return combined_df

def collect_all_event_logs_to_dataframe(runs_dir):
    all_dfs = []
    for root, dirs, files in os.walk(runs_dir):
        for file in files:
            if file.startswith("events.out.tfevents") and (not file.endswith('1735302178.6e7e7f5ca2e3.224.17')):
                event_file_path = os.path.join(root, file)
                print(f"Processing: {event_file_path}")
                df = extract_scalars_to_dataframe(event_file_path)
                df['source_dir'] = root
                all_dfs.append(df)

    combined_df = pd.concat(all_dfs, ignore_index=True)
    return combined_df

runs_dir = "/content/runs"
combined_df = collect_all_event_logs_to_dataframe(runs_dir)
combined_df = combined_df.drop(columns='batch_loss').dropna()
combined_df.loc[:, 'split'] = combined_df.loc[:, 'model'].apply(lambda x: x.split('_')[1])
combined_df.loc[:, 'model'] = combined_df.loc[:, 'model'].apply(lambda x: x.split('_')[0].lower())

combined_df.to_csv("combined_metrics.csv", index=False)

combined_df

