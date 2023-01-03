# python trainer.py --model_path=/tmp/model --config config/test.yaml
import os
os.environ['LD_LIBRARY_PATH'] = '/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH'

import torch
import pytorch_lightning as pl

from lib.args import parse_args
from lib.callbacks import HuggingFaceHubCallback, SampleCallback
from lib.model import load_model

from omegaconf import OmegaConf
from pytorch_lightning.callbacks import LearningRateMonitor, ModelCheckpoint
from pytorch_lightning.loggers import WandbLogger

args = parse_args()
config = OmegaConf.load(args.config)

def main(args):
    torch.manual_seed(config.trainer.seed)
    if args.model_path == None:
        args.model_path = config.trainer.model_path
    
    if config.trainer.precision == "fp16" and config.lightning.precision == 16:
        raise ValueError("Pure fp16 mode is not fully supported at this time. Please consider other configurations (trainer.precision and lightning.precision).")
    
    strategy = None
    tune = config.lightning.auto_scale_batch_size or config.lightning.auto_lr_find
    if config.lightning.accelerator in ["gpu", "cpu"] and not tune:
        strategy = "ddp_find_unused_parameters_false"
        config.lightning.replace_sampler_ddp = False
        
    if config.trainer.use_hivemind:
        from lib.hivemind import init_hivemind
        strategy = init_hivemind(config)
        
    model = load_model(args.model_path, config)

    # for ddp-optimize only
    # from torch.distributed.algorithms.ddp_comm_hooks import post_localSGD_hook as post_localSGD
    # strategy = pl.strategies.DDPStrategy(
    #     find_unused_parameters=False,
    #     gradient_as_bucket_view=True,
    #     ddp_comm_state=post_localSGD.PostLocalSGDState(
    #         process_group=None,
    #         subgroup=None,
    #         start_localSGD_iter=8,
    #     ),
    #     ddp_comm_hook=post_localSGD.post_localSGD_hook,
    #     model_averaging_period=4,
    # )
    
    # for experiment only
    # from experiment.attn_realign import AttnRealignModel
    # model = AttnRealignModel(args.model_path, config, config.trainer.init_batch_size)
    # from experiment.lora import LoRADiffusionModel
    # model = LoRADiffusionModel(args.model_path, config, config.trainer.init_batch_size)
    # from experiment.kwlenc import MixinModel
    # model = MixinModel(args.model_path, config, config.trainer.init_batch_size)
    
    callbacks = []
    if config.monitor.huggingface_repo != "":
        hf_logger = HuggingFaceHubCallback(
            repo_name=config.monitor.huggingface_repo, 
            use_auth_token=config.monitor.hf_auth_token,
            **config.monitor
        )
        callbacks.append(hf_logger)
    
    logger = None
    if config.monitor.wandb_id != "":
        logger = WandbLogger(project=config.monitor.wandb_id)
        callbacks.append(LearningRateMonitor(logging_interval='step'))
        
    if config.get("custom_embeddings") != None and config.custom_embeddings.enabled:
        from experiment.textual_inversion import CustomEmbeddingsCallback
        callbacks.append(CustomEmbeddingsCallback(config.custom_embeddings))
        
    sp =  config.get("sampling")
    if sp != None and sp.enabled:
        callbacks.append(SampleCallback(sp, logger))
    
    callbacks.append(ModelCheckpoint(**config.checkpoint))
    trainer = pl.Trainer(
        logger=logger, 
        strategy=strategy, 
        callbacks=callbacks,
        **config.lightning
    )
    
    if trainer.auto_scale_batch_size or trainer.auto_lr_find:
        trainer.tune(model=model, scale_batch_size_kwargs={"steps_per_trial": 5})
    
    trainer.fit(
        model=model,
        ckpt_path=args.resume if args.resume else None
    )

if __name__ == "__main__":
    args = parse_args()
    main(args)
