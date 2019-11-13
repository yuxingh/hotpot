# -*- coding: utf-8 -*-
"""
Spyder Editor

This is a temporary script file.
"""

import json
from nltk import tokenize

result = []
squad = None
with open('train-v2.0.json') as f:
    squad = json.load(f)
    squad = squad['data']
for item in squad:
    title = item['title']
    for paragraph in item['paragraphs']:
        context = tokenize.sent_tokenize(paragraph['context'])
        for qas in paragraph['qas']:
            if qas['is_impossible']:
                continue
            c_q = {}
            c_q['_id'] = qas['id']
            c_q['question'] = qas['question']
            c_q['answer'] = qas['answers'][0]['text']
            c_q['context'] = [[title, context]]
            c_q['level'] = 'easy'
            c_q['type'] = 'bridge'
            c_q['supporting_facts'] = []
            for i in range(len(context)):
                if c_q['answer'] in context[i]:
                    c_q['supporting_facts'].append([title, i])
            result.append(c_q)

original = None
with open('hotpot_train_v1.1.json') as f:
    original = json.load(f)
    original.extend(result)
with open('hotpot_train_v1.2.json', 'w') as f:
    json.dump(original, f)
with open('hotpot_train_squad.json', 'w') as f:
    json.dump(result, f)