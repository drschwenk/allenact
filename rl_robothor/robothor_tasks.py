from typing import Tuple, List, Dict, Any, Optional

import gym
import numpy as np
from ai2thor.util.metrics import compute_single_spl

from rl_base.common import RLStepResult
from rl_base.sensor import Sensor
from rl_base.task import Task
from rl_robothor.robothor_constants import (
    MOVE_AHEAD,
    ROTATE_LEFT,
    ROTATE_RIGHT,
    END,
    LOOK_UP,
    LOOK_DOWN,
)
from rl_robothor.robothor_environment import RoboThorEnvironment
from utils.cache_utils import get_distance, get_distance_to_object
from utils.system import get_logger


class PointNavTask(Task[RoboThorEnvironment]):
    _actions = (MOVE_AHEAD, ROTATE_LEFT, ROTATE_RIGHT, END)

    def __init__(
        self,
        env: RoboThorEnvironment,
        sensors: List[Sensor],
        task_info: Dict[str, Any],
        max_steps: int,
        reward_configs: Dict[str, Any],
        distance_cache: Optional[Dict[str, Any]] = None,
        episode_info: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> None:
        super().__init__(
            env=env, sensors=sensors, task_info=task_info, max_steps=max_steps, **kwargs
        )
        self.reward_configs = reward_configs
        self._took_end_action: bool = False
        self._success: Optional[bool] = False
        self.distance_cache = distance_cache

        if episode_info:
            self.episode_optimal_corners = episode_info["shortest_path"]
            dist = episode_info["shortest_path_length"]
        else:
            self.episode_optimal_corners = list(
                self.env.path_corners(task_info["target"])
            )  # assume it's valid (sampler must take care)!
            dist = self.env.path_corners_to_dist(self.episode_optimal_corners)
        if dist == float("inf"):
            get_logger().warning(
                "No path for {} from {} to {}".format(
                    self.env.scene_name, self.env.agent_state(), task_info["target"]
                )
            )

        if self.distance_cache:
            self.last_geodesic_distance = get_distance(
                self.distance_cache, self.env.agent_state(), self.task_info["target"]
            )
        else:
            self.last_geodesic_distance = self.env.dist_to_object(
                self.task_info["object_type"]
            )

        self.optimal_distance = self.last_geodesic_distance
        self._rewards = []
        self._distance_to_goal = []
        self._metrics = None
        self.path = []
        self.num_moves_made = 0

    @property
    def action_space(self):
        return gym.spaces.Discrete(len(self._actions))

    def reached_terminal_state(self) -> bool:
        return self._took_end_action

    @classmethod
    def class_action_names(cls, **kwargs) -> Tuple[str, ...]:
        return cls._actions

    def close(self) -> None:
        self.env.stop()

    def _step(self, action: int) -> RLStepResult:
        action_str = self.action_names()[action]

        if action_str == END:
            self._took_end_action = True
            self._success = self._is_goal_in_range()
            self.last_action_success = self._success
        else:
            self.env.step({"action": action_str})
            self.last_action_success = self.env.last_action_success
            pose = self.env.agent_state()
            self.path.append({k: pose[k] for k in ["x", "y", "z"]})
        if len(self.path) > 1 and self.path[-1] != self.path[-2]:
            self.num_moves_made += 1
        step_result = RLStepResult(
            observation=self.get_observations(),
            reward=self.judge(),
            done=self.is_done(),
            info={"last_action_success": self.last_action_success, "action": action},
        )
        return step_result

    def render(self, mode: str = "rgb", *args, **kwargs) -> np.ndarray:
        assert mode in ["rgb", "depth"], "only rgb and depth rendering is implemented"
        if mode == "rgb":
            return self.env.current_frame
        elif mode == "depth":
            return self.env.current_depth

    def _is_goal_in_range(self) -> Optional[bool]:
        tget = self.task_info["target"]
        if self.distance_cache:
            dist = get_distance(
                self.distance_cache, self.env.agent_state(), self.task_info["target"]
            )
        else:
            dist = self.dist_to_target()

        if -0.5 < dist <= 0.2:
            return True
        elif dist > 0.2:
            return False
        else:
            get_logger().warning(
                "No path for {} from {} to {}".format(
                    self.env.scene_name, self.env.agent_state(), tget
                )
            )
            return None

    def shaping(self) -> float:
        rew = 0.0

        if self.reward_configs["shaping_weight"] == 0.0:
            return rew

        if self.distance_cache:
            geodesic_distance = get_distance(
                self.distance_cache, self.env.agent_state(), self.task_info["target"]
            )
        else:
            geodesic_distance = self.dist_to_target()

        if geodesic_distance == -1.0:
            geodesic_distance = self.last_geodesic_distance
        if (
            self.last_geodesic_distance > -0.5 and geodesic_distance > -0.5
        ):  # (robothor limits)
            rew += self.last_geodesic_distance - geodesic_distance
        self.last_geodesic_distance = geodesic_distance

        return rew * self.reward_configs["shaping_weight"]

    def judge(self) -> float:
        """Judge the last event."""
        reward = self.reward_configs["step_penalty"]

        reward += self.shaping()

        if self._took_end_action:
            if self._success is not None:
                reward += (
                    self.reward_configs["goal_success_reward"]
                    if self._success
                    else self.reward_configs["failed_stop_reward"]
                )

        self._rewards.append(float(reward))
        return float(reward)

    def spl(self):
        if not self._success:
            return 0.0
        if self.distance_cache:
            li = self.optimal_distance
            pi = self.num_moves_made * self.env.config["gridSize"]
            res = li / (max(pi, li))
        else:
            res = compute_single_spl(
                self.path, self.episode_optimal_corners, self._success
            )
        return res

    def dist_to_target(self):
        res = self.env.dist_to_point(self.task_info["target"])
        return res if res > -0.5 else None

    def metrics(self) -> Dict[str, Any]:
        if not self.is_done():
            return {}
        else:
            total_reward = float(np.sum(self._rewards))
            self._rewards = []

            if self._success is None:
                return {}

            if self.distance_cache:
                dist2tget = get_distance(
                    self.distance_cache,
                    self.env.agent_state(),
                    self.task_info["target"],
                )
            else:
                raise NotImplementedError
            if dist2tget is None:
                return {}

            spl = self.spl()
            if spl is None:
                return {}

            return {
                "success": self._success,  # False also if no path to target
                "ep_length": self.num_steps_taken(),
                "total_reward": total_reward,
                "dist_to_target": dist2tget,
                "spl": spl,
                "task_info": self.task_info,
            }


class ObjectNavTask(Task[RoboThorEnvironment]):
    _actions = (MOVE_AHEAD, ROTATE_LEFT, ROTATE_RIGHT, END, LOOK_UP, LOOK_DOWN)

    def __init__(
        self,
        env: RoboThorEnvironment,
        sensors: List[Sensor],
        task_info: Dict[str, Any],
        max_steps: int,
        reward_configs: Dict[str, Any],
        distance_cache: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> None:
        super().__init__(
            env=env, sensors=sensors, task_info=task_info, max_steps=max_steps, **kwargs
        )
        self.reward_configs = reward_configs
        self._took_end_action: bool = False
        self._success: Optional[bool] = False
        self.mirror = task_info["mirrored"]
        self.distance_cache = distance_cache
        if self.distance_cache:
            self.last_geodesic_distance = get_distance_to_object(
                self.distance_cache,
                self.env.agent_state(),
                self.task_info["object_type"],
            )
        else:
            self.last_geodesic_distance = self.env.dist_to_object(
                self.task_info["object_type"]
            )
        self._rewards = []
        self._distance_to_goal = []
        self._metrics = None
        self.path = []
        self.task_info["followed_path"] = [self.env.agent_state()]
        self.task_info["taken_actions"] = []
        self.task_info["action_names"] = self.action_names()

        if not task_info["distance_to_target"]:
            self.episode_optimal_corners = self.env.path_corners(task_info["target"])
        self.num_moves_made = 0
        self.optimal_distance = self.last_geodesic_distance

    @property
    def action_space(self):
        return gym.spaces.Discrete(len(self._actions))

    def reached_terminal_state(self) -> bool:
        return self._took_end_action

    @classmethod
    def class_action_names(cls, **kwargs) -> Tuple[str, ...]:
        return cls._actions

    def close(self) -> None:
        self.env.stop()

    def _step(self, action: int) -> RLStepResult:
        action_str = self.action_names()[action]

        if self.mirror:
            if action_str == ROTATE_RIGHT:
                action_str = ROTATE_LEFT
            elif action_str == ROTATE_LEFT:
                action_str = ROTATE_RIGHT

        self.task_info["taken_actions"].append(action_str)

        if action_str == END:
            self._took_end_action = True
            self._success = self._is_goal_in_range()
            self.last_action_success = self._success
        else:
            self.env.step({"action": action_str})
            self.last_action_success = self.env.last_action_success
            pose = self.env.agent_state()
            self.path.append({k: pose[k] for k in ["x", "y", "z"]})
            self.task_info["followed_path"].append(pose)
        if len(self.path) > 1 and self.path[-1] != self.path[-2]:
            self.num_moves_made += 1
        step_result = RLStepResult(
            observation=self.get_observations(),
            reward=self.judge(),
            done=self.is_done(),
            info={"last_action_success": self.last_action_success, "action": action},
        )
        return step_result

    def render(self, mode: str = "rgb", *args, **kwargs) -> np.ndarray:
        assert mode in ["rgb", "depth"], "only rgb and depth rendering is implemented"
        if mode == "rgb":
            frame = self.env.current_frame.copy()
        elif mode == "depth":
            frame = self.env.current_depth.copy()
        if self.mirror:
            frame = frame[:, ::-1, :].copy()
        return frame

    def _is_goal_in_range(self) -> bool:
        return any(
            o["objectType"] == self.task_info["object_type"]
            for o in self.env.visible_objects()
        )

    def shaping(self) -> float:
        rew = 0.0

        if self.reward_configs["shaping_weight"] == 0.0:
            return rew

        if self.distance_cache:
            geodesic_distance = get_distance_to_object(
                self.distance_cache,
                self.env.agent_state(),
                self.task_info["object_type"],
            )
        else:
            geodesic_distance = self.env.dist_to_object(self.task_info["object_type"])
        if self.last_geodesic_distance > -0.5 and geodesic_distance > -0.5:
            rew += self.last_geodesic_distance - geodesic_distance
        self.last_geodesic_distance = geodesic_distance

        return rew * self.reward_configs["shaping_weight"]

    def judge(self) -> float:
        """Judge the last event."""
        reward = self.reward_configs["step_penalty"]

        reward += self.shaping()

        if self._took_end_action:
            reward += (
                self.reward_configs["goal_success_reward"]
                if self._success
                else self.reward_configs["failed_stop_reward"]
            )

        self._rewards.append(float(reward))
        return float(reward)

    def spl(self):
        if not self._success:
            return 0.0
        if self.distance_cache:
            li = self.optimal_distance
            pi = self.num_moves_made * self.env.config["gridSize"]
            res = li / (max(pi, li))
        else:
            res = compute_single_spl(
                self.path, self.episode_optimal_corners, self._success
            )
        return res

    def get_observations(self) -> Any:
        obs = self.sensor_suite.get_observations(env=self.env, task=self)
        if self.mirror:
            for o in obs:
                if ("rgb" in o or "depth" in o) and isinstance(obs[o], np.ndarray):
                    if len(obs[o].shape) == 3:
                        obs[o] = obs[o][:, ::-1, :].copy()
                    elif len(obs[o].shape) == 2:
                        obs[o] = obs[o][:, ::-1].copy()
        return obs

    def metrics(self) -> Dict[str, Any]:
        if self.distance_cache:
            dist2tget = get_distance_to_object(
                self.distance_cache,
                self.env.agent_state(),
                self.task_info["object_type"],
            )
        else:
            raise NotImplementedError
        if not self.is_done():
            return {}
        else:
            metrics = {
                "success": self._success,
                "ep_length": self.num_steps_taken(),
                "total_reward": np.sum(self._rewards),
                "dist_to_target": dist2tget,
                "spl": self.spl(),
                "task_info": self.task_info,
            }
            self._rewards = []
            return metrics
