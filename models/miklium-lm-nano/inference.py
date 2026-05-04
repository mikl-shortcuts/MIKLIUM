import json
import numpy as np
import re
import os

class MikliumNanoInference:
    def __init__(self, model_path):
        with open(model_path, 'r') as f:
            data = json.load(f)
        
        self.uchars = data['vocab']
        self.idx_to_token = {int(k) if k.isdigit() else k: v for k, v in data['idx_to_token'].items()}
        self.token_to_idx = {v: int(k) if k.isdigit() else k for k, v in data['idx_to_token'].items()}
        
        self.params = {k: np.array(v) for k, v in data['params'].items()}
        self.n_layer = data['n_layer']
        self.n_embd = data['n_embd']
        self.block_size = data['block_size']
        self.n_head = data.get('n_head', 4)
        self.head_dim = self.n_embd // self.n_head
        
        # BOS is usually len(uchars) based on model.py
        self.BOS = len(self.uchars)
        self.idx_to_token[self.BOS] = "<|endoftext|>"

    def get_tokens(self, text):
        return re.findall(r"<[^>]+>|[\w']+|[^\w\s\n]|\s+", text)

    def rmsnorm_fwd(self, x):
        ms = np.mean(x**2, axis=-1, keepdims=True)
        scale = (ms + 1e-5)**-0.5
        return x * scale

    def forward(self, tokens):
        T = len(tokens)
        idx = np.arange(T)
        x = self.params['wte'][tokens] + self.params['wpe'][idx]
        
        for i in range(self.n_layer):
            # Attention
            x_norm1 = self.rmsnorm_fwd(x)
            q = x_norm1 @ self.params[f'layer{i}.attn_wq'].T
            k = x_norm1 @ self.params[f'layer{i}.attn_wk'].T
            v = x_norm1 @ self.params[f'layer{i}.attn_wv'].T
            
            q = q.reshape(T, self.n_head, self.head_dim).transpose(1, 0, 2)
            k = k.reshape(T, self.n_head, self.head_dim).transpose(1, 0, 2)
            v = v.reshape(T, self.n_head, self.head_dim).transpose(1, 0, 2)
            
            att = (q @ k.transpose(0, 2, 1)) / (self.head_dim**0.5)
            mask = np.triu(np.ones((T, T)), k=1) * -1e10
            att = att + mask
            
            att_max = np.max(att, axis=-1, keepdims=True)
            exp_att = np.exp(att - att_max)
            probs = exp_att / (np.sum(exp_att, axis=-1, keepdims=True) + 1e-10)
            
            y_attn_heads = (probs @ v).transpose(1, 0, 2).reshape(T, self.n_embd)
            y_out = y_attn_heads @ self.params[f'layer{i}.attn_wo'].T
            x = x + y_out
            
            # MLP
            x_norm2 = self.rmsnorm_fwd(x)
            h = x_norm2 @ self.params[f'layer{i}.mlp_fc1'].T
            h_relu = np.maximum(0, h)
            y_mlp = h_relu @ self.params[f'layer{i}.mlp_fc2'].T
            x = x + y_mlp
            
        logits = x @ self.params['lm_head'].T
        return logits

    def generate(self, prompt, length=100, temp=0.7):
        prompt_tokens = self.get_tokens(prompt)
        tokens = [self.BOS] + [self.token_to_idx[t] for t in prompt_tokens if t in self.token_to_idx]
        
        start_offset = len(tokens)
        for _ in range(length):
            ctx = tokens[-self.block_size:]
            logits = self.forward(np.array(ctx))
            logits_last = logits[-1] / (temp + 1e-6)
            exp_l = np.exp(logits_last - np.max(logits_last))
            probs = exp_l / np.sum(exp_l)
            next_token = np.random.choice(len(probs), p=probs)
            if next_token == self.BOS: break
            tokens.append(next_token)
        
        out = ""
        for t in tokens[start_offset:]:
            if t == self.BOS: break
            out += self.idx_to_token.get(t, "")
        return out
