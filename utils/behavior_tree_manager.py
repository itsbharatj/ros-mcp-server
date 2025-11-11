import asyncio
import json
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import py_trees
from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Parallel, Selector, Sequence


class BTNodeType(Enum):
    SEQUENCE = "sequence"
    SELECTOR = "selector"
    PARALLEL = "parallel"
    ACTION = "action"
    SERVICE = "service"
    CONDITION = "condition"
    DELAY = "delay"


class ROSActionBehaviour(Behaviour):
    def __init__(
        self,
        name: str,
        action_name: str,
        action_type: str,
        goal: dict,
        send_action_fn: Callable,
        timeout: float = 10.0,
    ):
        super().__init__(name=name)
        self.action_name = action_name
        self.action_type = action_type
        self.goal = goal
        self.send_action_fn = send_action_fn
        self.timeout = timeout
        self.result = None
        self.goal_id = None

    def setup(self, **kwargs):
        self.logger.debug(f"Setting up action: {self.action_name}")

    def initialise(self):
        self.logger.debug(f"Initializing action: {self.action_name}")
        self.result = None
        self.goal_id = None

    def update(self):
        if self.result is None:
            result = self.send_action_fn(
                self.action_name, self.action_type, self.goal, self.timeout
            )

            if "error" in result:
                self.logger.error(f"Action failed: {result['error']}")
                self.feedback_message = f"Error: {result['error']}"
                return Status.FAILURE

            if result.get("success"):
                self.result = result
                self.goal_id = result.get("goal_id")
                self.feedback_message = f"Action completed: {self.action_name}"
                return Status.SUCCESS
            else:
                self.feedback_message = "Action in progress"
                return Status.RUNNING

        return Status.SUCCESS

    def terminate(self, new_status):
        self.logger.debug(
            f"Terminating action: {self.action_name} with status: {new_status}"
        )


class ROSServiceBehaviour(Behaviour):
    def __init__(
        self,
        name: str,
        service_name: str,
        service_type: str,
        request: dict,
        call_service_fn: Callable,
        timeout: float = 5.0,
    ):
        super().__init__(name=name)
        self.service_name = service_name
        self.service_type = service_type
        self.request = request
        self.call_service_fn = call_service_fn
        self.timeout = timeout
        self.result = None

    def setup(self, **kwargs):
        self.logger.debug(f"Setting up service: {self.service_name}")

    def initialise(self):
        self.logger.debug(f"Initializing service: {self.service_name}")
        self.result = None

    def update(self):
        if self.result is None:
            result = self.call_service_fn(
                self.service_name, self.service_type, self.request, self.timeout
            )

            if "error" in result:
                self.logger.error(f"Service failed: {result['error']}")
                self.feedback_message = f"Error: {result['error']}"
                return Status.FAILURE

            self.result = result
            self.feedback_message = f"Service completed: {self.service_name}"
            return Status.SUCCESS

        return Status.SUCCESS

    def terminate(self, new_status):
        self.logger.debug(
            f"Terminating service: {self.service_name} with status: {new_status}"
        )


class DelayBehaviour(Behaviour):
    def __init__(self, name: str, duration: float):
        super().__init__(name=name)
        self.duration = duration
        self.start_time = None

    def setup(self, **kwargs):
        self.logger.debug(f"Setting up delay: {self.duration}s")

    def initialise(self):
        self.start_time = time.time()
        self.logger.debug(f"Starting delay: {self.duration}s")

    def update(self):
        elapsed = time.time() - self.start_time
        if elapsed >= self.duration:
            self.feedback_message = f"Delay completed: {self.duration}s"
            return Status.SUCCESS
        else:
            remaining = self.duration - elapsed
            self.feedback_message = f"Waiting: {remaining:.1f}s remaining"
            return Status.RUNNING

    def terminate(self, new_status):
        self.logger.debug(f"Terminating delay with status: {new_status}")


class ConditionBehaviour(Behaviour):
    def __init__(self, name: str, condition_fn: Callable, condition_args: dict = None):
        super().__init__(name=name)
        self.condition_fn = condition_fn
        self.condition_args = condition_args or {}

    def setup(self, **kwargs):
        self.logger.debug(f"Setting up condition: {self.name}")

    def initialise(self):
        self.logger.debug(f"Initializing condition: {self.name}")

    def update(self):
        result = self.condition_fn(**self.condition_args)
        if result:
            self.feedback_message = f"Condition met: {self.name}"
            return Status.SUCCESS
        else:
            self.feedback_message = f"Condition not met: {self.name}"
            return Status.FAILURE

    def terminate(self, new_status):
        self.logger.debug(f"Terminating condition: {self.name} with status: {new_status}")


class BehaviorTreeManager:
    def __init__(self, send_action_fn: Callable, call_service_fn: Callable):
        self.send_action_fn = send_action_fn
        self.call_service_fn = call_service_fn
        self.active_trees: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    def parse_json_tree(self, tree_json: dict) -> py_trees.behaviour.Behaviour:
        node_type = tree_json.get("type", "").lower()
        name = tree_json.get("name", "unnamed")
        children_data = tree_json.get("children", [])

        if node_type == BTNodeType.SEQUENCE.value:
            children = [self.parse_json_tree(child) for child in children_data]
            return Sequence(name=name, memory=False, children=children)

        elif node_type == BTNodeType.SELECTOR.value:
            children = [self.parse_json_tree(child) for child in children_data]
            return Selector(name=name, memory=False, children=children)

        elif node_type == BTNodeType.PARALLEL.value:
            children = [self.parse_json_tree(child) for child in children_data]
            policy = tree_json.get("policy", "SuccessOnAll")
            if policy == "SuccessOnOne":
                sync_policy = Parallel.SuccessOnOne()
            else:
                sync_policy = Parallel.SuccessOnAll()
            return Parallel(name=name, policy=sync_policy, children=children)

        elif node_type == BTNodeType.ACTION.value:
            action_name = tree_json.get("action_name")
            action_type = tree_json.get("action_type")
            goal = tree_json.get("goal", {})
            timeout = tree_json.get("timeout", 10.0)

            if not action_name or not action_type:
                raise ValueError(
                    f"Action node '{name}' missing required fields: action_name, action_type"
                )

            return ROSActionBehaviour(
                name=name,
                action_name=action_name,
                action_type=action_type,
                goal=goal,
                send_action_fn=self.send_action_fn,
                timeout=timeout,
            )

        elif node_type == BTNodeType.SERVICE.value:
            service_name = tree_json.get("service_name")
            service_type = tree_json.get("service_type")
            request = tree_json.get("request", {})
            timeout = tree_json.get("timeout", 5.0)

            if not service_name or not service_type:
                raise ValueError(
                    f"Service node '{name}' missing required fields: service_name, service_type"
                )

            return ROSServiceBehaviour(
                name=name,
                service_name=service_name,
                service_type=service_type,
                request=request,
                call_service_fn=self.call_service_fn,
                timeout=timeout,
            )

        elif node_type == BTNodeType.DELAY.value:
            duration = tree_json.get("duration", 1.0)
            return DelayBehaviour(name=name, duration=duration)

        elif node_type == BTNodeType.CONDITION.value:
            raise NotImplementedError(
                "Condition nodes require custom condition functions - not yet supported in JSON parsing"
            )

        else:
            raise ValueError(f"Unknown node type: {node_type}")

    def parse_xml_tree(self, tree_xml: str) -> py_trees.behaviour.Behaviour:
        root = ET.fromstring(tree_xml)
        return self._parse_xml_node(root)

    def _parse_xml_node(self, element: ET.Element) -> py_trees.behaviour.Behaviour:
        tag = element.tag.lower()
        name = element.get("name", "unnamed")

        if tag == "sequence":
            children = [self._parse_xml_node(child) for child in element]
            return Sequence(name=name, memory=False, children=children)

        elif tag == "selector":
            children = [self._parse_xml_node(child) for child in element]
            return Selector(name=name, memory=False, children=children)

        elif tag == "parallel":
            children = [self._parse_xml_node(child) for child in element]
            policy = element.get("policy", "SuccessOnAll")
            if policy == "SuccessOnOne":
                sync_policy = Parallel.SuccessOnOne()
            else:
                sync_policy = Parallel.SuccessOnAll()
            return Parallel(name=name, policy=sync_policy, children=children)

        elif tag == "action":
            action_name = element.get("action_name")
            action_type = element.get("action_type")
            goal_text = element.get("goal", "{}")
            goal = json.loads(goal_text)
            timeout = float(element.get("timeout", "10.0"))

            if not action_name or not action_type:
                raise ValueError(
                    f"Action node '{name}' missing required attributes: action_name, action_type"
                )

            return ROSActionBehaviour(
                name=name,
                action_name=action_name,
                action_type=action_type,
                goal=goal,
                send_action_fn=self.send_action_fn,
                timeout=timeout,
            )

        elif tag == "service":
            service_name = element.get("service_name")
            service_type = element.get("service_type")
            request_text = element.get("request", "{}")
            request = json.loads(request_text)
            timeout = float(element.get("timeout", "5.0"))

            if not service_name or not service_type:
                raise ValueError(
                    f"Service node '{name}' missing required attributes: service_name, service_type"
                )

            return ROSServiceBehaviour(
                name=name,
                service_name=service_name,
                service_type=service_type,
                request=request,
                call_service_fn=self.call_service_fn,
                timeout=timeout,
            )

        elif tag == "delay":
            duration = float(element.get("duration", "1.0"))
            return DelayBehaviour(name=name, duration=duration)

        elif tag == "condition":
            raise NotImplementedError(
                "Condition nodes require custom condition functions - not yet supported in XML parsing"
            )

        else:
            raise ValueError(f"Unknown XML tag: {tag}")

    def create_tree(
        self, tree_definition: str, format: str = "json", context: dict = None
    ) -> str:
        tree_id = f"bt_{uuid.uuid4().hex[:8]}"

        if format.lower() == "json":
            tree_dict = json.loads(tree_definition)
            root = self.parse_json_tree(tree_dict)
        elif format.lower() == "xml":
            root = self.parse_xml_tree(tree_definition)
        else:
            raise ValueError(f"Unsupported format: {format}. Use 'json' or 'xml'")

        tree = py_trees.trees.BehaviourTree(root=root)
        tree.setup(timeout=15)

        with self.lock:
            self.active_trees[tree_id] = {
                "tree": tree,
                "status": "created",
                "context": context or {},
                "created_at": time.time(),
                "snapshots": [],
            }

        return tree_id

    def execute_tree(self, tree_id: str, tick_rate: float = 10.0) -> dict:
        with self.lock:
            if tree_id not in self.active_trees:
                return {"error": f"Tree {tree_id} not found"}

            tree_data = self.active_trees[tree_id]
            tree = tree_data["tree"]

        tree_data["status"] = "running"
        tree_data["start_time"] = time.time()

        tick_count = 0
        max_ticks = 1000
        sleep_time = 1.0 / tick_rate

        while tick_count < max_ticks:
            tree.tick()
            tick_count += 1

            snapshot = {
                "tick": tick_count,
                "timestamp": time.time(),
                "status": tree.root.status.name,
                "feedback": getattr(tree.root, "feedback_message", ""),
            }
            tree_data["snapshots"].append(snapshot)

            if tree.root.status in [Status.SUCCESS, Status.FAILURE]:
                tree_data["status"] = "completed"
                tree_data["final_status"] = tree.root.status.name
                tree_data["end_time"] = time.time()
                tree_data["total_ticks"] = tick_count
                break

            time.sleep(sleep_time)

        if tick_count >= max_ticks:
            tree_data["status"] = "timeout"
            tree_data["final_status"] = "TIMEOUT"
            tree_data["end_time"] = time.time()
            tree_data["total_ticks"] = tick_count

        return {
            "tree_id": tree_id,
            "status": tree_data["status"],
            "final_status": tree_data.get("final_status", "UNKNOWN"),
            "total_ticks": tick_count,
            "duration": tree_data["end_time"] - tree_data["start_time"],
        }

    async def execute_tree_async(self, tree_id: str, tick_rate: float = 10.0) -> dict:
        with self.lock:
            if tree_id not in self.active_trees:
                return {"error": f"Tree {tree_id} not found"}

            tree_data = self.active_trees[tree_id]
            tree = tree_data["tree"]

        tree_data["status"] = "running"
        tree_data["start_time"] = time.time()

        tick_count = 0
        max_ticks = 1000
        sleep_time = 1.0 / tick_rate

        while tick_count < max_ticks:
            tree.tick()
            tick_count += 1

            snapshot = {
                "tick": tick_count,
                "timestamp": time.time(),
                "status": tree.root.status.name,
                "feedback": getattr(tree.root, "feedback_message", ""),
            }
            tree_data["snapshots"].append(snapshot)

            if tree.root.status in [Status.SUCCESS, Status.FAILURE]:
                tree_data["status"] = "completed"
                tree_data["final_status"] = tree.root.status.name
                tree_data["end_time"] = time.time()
                tree_data["total_ticks"] = tick_count
                break

            await asyncio.sleep(sleep_time)

        if tick_count >= max_ticks:
            tree_data["status"] = "timeout"
            tree_data["final_status"] = "TIMEOUT"
            tree_data["end_time"] = time.time()
            tree_data["total_ticks"] = tick_count

        return {
            "tree_id": tree_id,
            "status": tree_data["status"],
            "final_status": tree_data.get("final_status", "UNKNOWN"),
            "total_ticks": tick_count,
            "duration": tree_data["end_time"] - tree_data["start_time"],
        }

    def get_tree_status(self, tree_id: str) -> dict:
        with self.lock:
            if tree_id not in self.active_trees:
                return {"error": f"Tree {tree_id} not found"}

            tree_data = self.active_trees[tree_id]
            tree = tree_data["tree"]

            recent_snapshots = tree_data["snapshots"][-10:]

            return {
                "tree_id": tree_id,
                "status": tree_data["status"],
                "current_tick_status": tree.root.status.name,
                "total_snapshots": len(tree_data["snapshots"]),
                "recent_snapshots": recent_snapshots,
                "context": tree_data["context"],
            }

    def cancel_tree(self, tree_id: str) -> dict:
        with self.lock:
            if tree_id not in self.active_trees:
                return {"error": f"Tree {tree_id} not found"}

            tree_data = self.active_trees[tree_id]
            tree_data["status"] = "cancelled"
            tree_data["end_time"] = time.time()

            return {
                "tree_id": tree_id,
                "status": "cancelled",
                "message": f"Tree {tree_id} has been cancelled",
            }

    def delete_tree(self, tree_id: str) -> dict:
        with self.lock:
            if tree_id not in self.active_trees:
                return {"error": f"Tree {tree_id} not found"}

            del self.active_trees[tree_id]

            return {
                "tree_id": tree_id,
                "status": "deleted",
                "message": f"Tree {tree_id} has been deleted",
            }

    def list_trees(self) -> dict:
        with self.lock:
            trees_info = []
            for tree_id, tree_data in self.active_trees.items():
                trees_info.append(
                    {
                        "tree_id": tree_id,
                        "status": tree_data["status"],
                        "created_at": tree_data["created_at"],
                        "total_snapshots": len(tree_data["snapshots"]),
                    }
                )

            return {"trees": trees_info, "total_count": len(trees_info)}
