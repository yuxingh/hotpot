import ujson as json
import numpy as np
from tqdm import tqdm
import os
from torch import optim, nn
from model import Model #, NoCharModel, NoSelfModel
from sp_model import SPModel
# from normal_model import NormalModel, NoSelfModel, NoCharModel, NoSentModel
# from oracle_model import OracleModel, OracleModelV2
# from util import get_record_parser, convert_tokens, evaluate, get_batch_dataset, get_dataset
from util import convert_tokens, evaluate
from util import get_buckets, DataIterator, IGNORE_INDEX
import time
import shutil
import random
import torch
from torch.autograd import Variable
import sys
from torch.nn import functional as F

def create_exp_dir(path, scripts_to_save=None):
    if not os.path.exists(path):
        os.mkdir(path)

    print('Experiment dir : {}'.format(path))
    if scripts_to_save is not None:
        if not os.path.exists(os.path.join(path, 'scripts')):
            os.mkdir(os.path.join(path, 'scripts'))
        for script in scripts_to_save:
            dst_file = os.path.join(path, 'scripts', os.path.basename(script))
            shutil.copyfile(script, dst_file)

nll_sum = nn.CrossEntropyLoss(size_average=False, ignore_index=IGNORE_INDEX)
nll_average = nn.CrossEntropyLoss(size_average=True, ignore_index=IGNORE_INDEX)
nll_all = nn.CrossEntropyLoss(reduce=False, ignore_index=IGNORE_INDEX)

def train(config):
    with open(config.word_emb_file, "r") as fh:
        word_mat = np.array(json.load(fh), dtype=np.float32)
    with open(config.char_emb_file, "r") as fh:
        char_mat = np.array(json.load(fh), dtype=np.float32)
    with open(config.dev_eval_file, "r") as fh:
        dev_eval_file = json.load(fh)
    with open(config.idx2word_file, 'r') as fh:
        idx2word_dict = json.load(fh)

    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    torch.cuda.manual_seed_all(config.seed)

    config.save = '{}-{}'.format(config.save, time.strftime("%Y%m%d-%H%M%S"))
    create_exp_dir(config.save, scripts_to_save=['run.py', 'model.py', 'util.py', 'sp_model.py'])
    def logging(s, print_=True, log_=True):
        if print_:
            print(s)
        if log_:
            with open(os.path.join(config.save, 'log.txt'), 'a+') as f_log:
                f_log.write(s + '\n')

    logging('Config')
    for k, v in config.__dict__.items():
        logging('    - {} : {}'.format(k, v))

    logging("Building model...")
    train_buckets = get_buckets(config.train_record_file)
    train_buckets_squad = get_buckets('squad_record.pkl')#yxh
    dev_buckets = get_buckets(config.dev_record_file)

    def build_train_iterator(buckets=train_buckets, batch_size=config.batch_size):
        return DataIterator(train_buckets, batch_size, config.para_limit, config.ques_limit, config.char_limit, True, config.sent_limit)

    def build_dev_iterator():
        return DataIterator(dev_buckets, config.batch_size, config.para_limit, config.ques_limit, config.char_limit, False, config.sent_limit)

    if config.sp_lambda > 0:
        model = SPModel(config, word_mat, char_mat)
    else:
        model = Model(config, word_mat, char_mat)

    logging('nparams {}'.format(sum([p.nelement() for p in model.parameters() if p.requires_grad])))
    ori_model = model.cuda()
    #ori_model.load_state_dict(torch.load(os.path.join('HOTPOT-20191113-222741', 'model.pt')))#yxh
    model = nn.DataParallel(ori_model)

    lr = config.init_lr
    optimizer = optim.SGD(filter(lambda p: p.requires_grad, model.parameters()), lr=config.init_lr)
    cur_patience = 0
    total_loss = 0
    global_step = 0
    best_dev_F1 = None
    best_loss = None #yxh
    stop_train = False
    start_time = time.time()
    eval_start_time = time.time()
    model.train()
    
    for epoch in range(10000):
        #it2 = build_train_iterator(train_buckets_squad, 12)        
        for data in build_train_iterator(batch_size=24):
            context_idxs = Variable(data['context_idxs'])
            ques_idxs = Variable(data['ques_idxs'])
            context_char_idxs = Variable(data['context_char_idxs'])
            ques_char_idxs = Variable(data['ques_char_idxs'])
            context_lens = Variable(data['context_lens'])
            y1 = Variable(data['y1'])
            y2 = Variable(data['y2'])
            q_type = Variable(data['q_type'])
            is_support = Variable(data['is_support'])
            start_mapping = Variable(data['start_mapping'])
            end_mapping = Variable(data['end_mapping'])
            all_mapping = Variable(data['all_mapping'])

            logit1, logit2, predict_type, predict_support = model(context_idxs, ques_idxs, context_char_idxs, ques_char_idxs, context_lens, start_mapping, end_mapping, all_mapping, is_support=is_support, return_yp=False)
            loss_1 = (nll_sum(predict_type, q_type) + nll_sum(logit1, y1) + nll_sum(logit2, y2)) / context_idxs.size(0)
            loss_2 = nll_average(predict_support.view(-1, 2), is_support.view(-1))
            loss = loss_1 + config.sp_lambda * loss_2
            #loss = config.sp_lambda * nll_average(predict_support.view(-1, 2), is_support.view(-1))#yxh
            ###
            '''
            try:
                data2 = next(it2)
                context_idxs2 = Variable(data2['context_idxs'])
                ques_idxs2 = Variable(data2['ques_idxs'])
                context_char_idxs2 = Variable(data2['context_char_idxs'])
                ques_char_idxs2 = Variable(data2['ques_char_idxs'])
                context_lens2 = Variable(data2['context_lens'])
                y12 = Variable(data2['y1'])
                y22 = Variable(data2['y2'])
                q_type2 = Variable(data2['q_type'])
                is_support2 = Variable(data2['is_support'])
                start_mapping2 = Variable(data2['start_mapping'])
                end_mapping2 = Variable(data2['end_mapping'])
                all_mapping2 = Variable(data2['all_mapping'])
    
                logit12, logit22, predict_type2, predict_support2 = model(context_idxs2, ques_idxs2, context_char_idxs2, ques_char_idxs2, context_lens2, start_mapping2, end_mapping2, all_mapping2, return_yp=False)
                #loss_12 = (nll_sum(predict_type2, q_type2) + nll_sum(logit12, y12) + nll_sum(logit22, y22)) / context_idxs.size(0)
                loss_22 = nll_average(predict_support2.view(-1, 2), is_support2.view(-1))
                loss2 = config.sp_lambda * loss_22            
                loss = loss+loss2
            except:
                pass
            '''
            ###
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.data#[0]
            global_step += 1

            if global_step % config.period == 0:
                cur_loss = total_loss / config.period
                elapsed = time.time() - start_time
                logging('| epoch {:3d} | step {:6d} | lr {:05.5f} | ms/batch {:5.2f} | train loss {:8.3f}'.format(epoch, global_step, lr, elapsed*1000/config.period, cur_loss))
                total_loss = 0
                start_time = time.time()

            if global_step % config.checkpoint == 0:
                model.eval()
                metrics = evaluate_batch(build_dev_iterator(), model, 0, dev_eval_file, config)
                model.train()

                logging('-' * 89)
                logging('| eval {:6d} in epoch {:3d} | time: {:5.2f}s | dev loss {:8.3f} | EM {:.4f} | F1 {:.4f}'.format(global_step//config.checkpoint,
                    epoch, time.time()-eval_start_time, metrics['loss'], metrics['exact_match'], metrics['f1']))
                logging('-' * 89)

                eval_start_time = time.time()

                dev_F1 = metrics['f1']
                if best_dev_F1 is None or dev_F1 > best_dev_F1:
                    best_dev_F1 = dev_F1
                    torch.save(ori_model.state_dict(), os.path.join(config.save, 'model.pt'))
                    cur_patience = 0
                else:
                    cur_patience += 1
                    if cur_patience >= config.patience:
                        lr /= 2.0
                        for param_group in optimizer.param_groups:
                            param_group['lr'] = lr
                        if lr < config.init_lr * 1e-2:
                            stop_train = True
                            break
                        cur_patience = 0
            #break#yxh
        if stop_train: break
    logging('best_dev_F1 {}'.format(best_dev_F1))


def evaluate_batch(data_source, model, max_batches, eval_file, config):
    answer_dict = {}
    sp_dict = {}
    total_loss, step_cnt = 0, 0
    iter = data_source
    for step, data in enumerate(iter):
        if step >= max_batches and max_batches > 0: break

        context_idxs = Variable(data['context_idxs'], volatile=True)
        ques_idxs = Variable(data['ques_idxs'], volatile=True)
        context_char_idxs = Variable(data['context_char_idxs'], volatile=True)
        ques_char_idxs = Variable(data['ques_char_idxs'], volatile=True)
        context_lens = Variable(data['context_lens'], volatile=True)
        y1 = Variable(data['y1'], volatile=True)
        y2 = Variable(data['y2'], volatile=True)
        q_type = Variable(data['q_type'], volatile=True)
        is_support = Variable(data['is_support'], volatile=True)
        start_mapping = Variable(data['start_mapping'], volatile=True)
        end_mapping = Variable(data['end_mapping'], volatile=True)
        all_mapping = Variable(data['all_mapping'], volatile=True)

        logit1, logit2, predict_type, predict_support, yp1, yp2 = model(context_idxs, ques_idxs, context_char_idxs, ques_char_idxs, context_lens, start_mapping, end_mapping, all_mapping, is_support=is_support, return_yp=True)
        loss = (nll_sum(predict_type, q_type) + nll_sum(logit1, y1) + nll_sum(logit2, y2)) / context_idxs.size(0) + config.sp_lambda * nll_average(predict_support.view(-1, 2), is_support.view(-1))
        #loss = config.sp_lambda * nll_average(predict_support.view(-1, 2), is_support.view(-1))#yxh
        answer_dict_ = convert_tokens(eval_file, data['ids'], yp1.data.cpu().numpy().tolist(), yp2.data.cpu().numpy().tolist(), np.argmax(predict_type.data.cpu().numpy(), 1))
        answer_dict.update(answer_dict_)

        total_loss += loss.data#[0]
        step_cnt += 1
    loss = total_loss / step_cnt
    metrics = evaluate(eval_file, answer_dict)
    metrics['loss'] = loss

    return metrics

def predict(data_source, model, eval_file, config, prediction_file):
    answer_dict = {}
    sp_dict = {}
    sp_th = config.sp_threshold
    for step, data in enumerate(tqdm(data_source)):
        context_idxs = Variable(data['context_idxs'], volatile=True)
        ques_idxs = Variable(data['ques_idxs'], volatile=True)
        context_char_idxs = Variable(data['context_char_idxs'], volatile=True)
        ques_char_idxs = Variable(data['ques_char_idxs'], volatile=True)
        context_lens = Variable(data['context_lens'], volatile=True)
        start_mapping = Variable(data['start_mapping'], volatile=True)
        end_mapping = Variable(data['end_mapping'], volatile=True)
        all_mapping = Variable(data['all_mapping'], volatile=True)
        is_support = Variable(data['is_support'], volatile=True)#yxh

        logit1, logit2, predict_type, predict_support, yp1, yp2 = model(context_idxs, ques_idxs, context_char_idxs, ques_char_idxs, context_lens, start_mapping, end_mapping, all_mapping, is_support=is_support, return_yp=True)
        answer_dict_ = convert_tokens(eval_file, data['ids'], yp1.data.cpu().numpy().tolist(), yp2.data.cpu().numpy().tolist(), np.argmax(predict_type.data.cpu().numpy(), 1))
        answer_dict.update(answer_dict_)

        predict_support_np = torch.sigmoid(predict_support[:, :, 1]).data.cpu().numpy()
        for i in range(predict_support_np.shape[0]):
            cur_sp_pred = []
            cur_id = data['ids'][i]
            for j in range(predict_support_np.shape[1]):
                if j >= len(eval_file[cur_id]['sent2title_ids']): break
                if predict_support_np[i, j] > sp_th:
                    cur_sp_pred.append(eval_file[cur_id]['sent2title_ids'][j])
            sp_dict.update({cur_id: cur_sp_pred})

    prediction = {'answer': answer_dict, 'sp': sp_dict}
    with open(prediction_file, 'w') as f:
        json.dump(prediction, f)

def test(config):
    with open(config.word_emb_file, "r") as fh:
        word_mat = np.array(json.load(fh), dtype=np.float32)
    with open(config.char_emb_file, "r") as fh:
        char_mat = np.array(json.load(fh), dtype=np.float32)
    if config.data_split == 'dev':
        with open(config.dev_eval_file, "r") as fh:
            dev_eval_file = json.load(fh)
    else:
        with open(config.test_eval_file, 'r') as fh:
            dev_eval_file = json.load(fh)
    with open(config.idx2word_file, 'r') as fh:
        idx2word_dict = json.load(fh)

    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    torch.cuda.manual_seed_all(config.seed)

    def logging(s, print_=True, log_=True):
        if print_:
            print(s)
        if log_:
            with open(os.path.join(config.save, 'log.txt'), 'a+') as f_log:
                f_log.write(s + '\n')

    if config.data_split == 'dev':
        dev_buckets = get_buckets(config.dev_record_file)
        para_limit = config.para_limit
        ques_limit = config.ques_limit
    elif config.data_split == 'test':
        para_limit = None
        ques_limit = None
        dev_buckets = get_buckets(config.test_record_file)

    def build_dev_iterator():
        return DataIterator(dev_buckets, config.batch_size, para_limit,
            ques_limit, config.char_limit, False, config.sent_limit)

    if config.sp_lambda > 0:
        model = SPModel(config, word_mat, char_mat)
    else:
        model = Model(config, word_mat, char_mat)
    ori_model = model.cuda()
    ori_model.load_state_dict(torch.load(os.path.join(config.save, 'model.pt')))
    model = nn.DataParallel(ori_model)

    model.eval()
    predict(build_dev_iterator(), model, dev_eval_file, config, config.prediction_file)

