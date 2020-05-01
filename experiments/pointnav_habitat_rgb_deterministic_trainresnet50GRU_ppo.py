import gym
import torch.nn as nn
from torchvision import models

from models.point_nav_models import PointNavActorCriticTrainResNet50GRU
from rl_base.sensor import SensorSuite
from rl_habitat.habitat_tasks import PointNavTask
from rl_habitat.habitat_sensors import RGBSensorHabitat, TargetCoordinatesSensorHabitat
from rl_habitat.habitat_preprocessors import ResnetPreProcessorHabitat
from rl_habitat.habitat_utils import construct_env_configs
from experiments.pointnav_habitat_base import PointNavHabitatBaseExperimentConfig


class PointNavHabitatRGBDeterministicResNet50GRUPPOExperimentConfig(PointNavHabitatBaseExperimentConfig):
    """A Point Navigation experiment configuraqtion in Habitat"""

    SENSORS = [
        RGBSensorHabitat(
            {
                "height": PointNavHabitatBaseExperimentConfig.SCREEN_SIZE,
                "width": PointNavHabitatBaseExperimentConfig.SCREEN_SIZE,
                "use_resnet_normalization": True,
            }
        ),
        TargetCoordinatesSensorHabitat({"coordinate_dims": 2}),
    ]

    PREPROCESSORS = [
        ResnetPreProcessorHabitat(
            config={
                "input_height": PointNavHabitatBaseExperimentConfig.SCREEN_SIZE,
                "input_width": PointNavHabitatBaseExperimentConfig.SCREEN_SIZE,
                "output_width": 1,
                "output_height": 1,
                "output_dims": 2048,
                "pool": False,
                "torchvision_resnet_model": models.resnet50,
                "input_uuids": ["rgb"],
                "output_uuid": "rgb_resnet",
            }
        ),
    ]

    OBSERVATIONS = [
        "rgb_resnet",
        "target_coordinates_ind",
    ]

    CONFIG = PointNavHabitatBaseExperimentConfig.CONFIG.clone()
    CONFIG.SIMULATOR.AGENT_0.SENSORS = ['RGB_SENSOR']

    TRAIN_CONFIGS = construct_env_configs(CONFIG)

    @classmethod
    def create_model(cls, **kwargs) -> nn.Module:
        return PointNavActorCriticTrainResNet50GRU(
            action_space=gym.spaces.Discrete(len(PointNavTask.action_names())),
            observation_space=SensorSuite(cls.SENSORS).observation_spaces,
            goal_sensor_uuid="target_coordinates_ind",
            hidden_size=512,
            embed_coordinates=False,
            coordinate_dims=2,
            num_rnn_layers=1,
            rnn_type='GRU'
        )
