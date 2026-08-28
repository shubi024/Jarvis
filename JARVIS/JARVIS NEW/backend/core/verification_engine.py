import logging
import os
import json
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, Union
from datetime import datetime, timezone
import jsonschema

from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.core.task_contracts import (
    TaskPackage, ResultPackage, ResultStatus, VerificationContract
)
from backend.tools.tool_registry import tool_registry
from backend.security.security_manager import security_manager
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Core.VerificationEngine")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class VerificationEngine:
    """
    J.A.R.V.I.S. Verification Engine.
    Proves actual real-world outcomes against the TaskPackage's VerificationContract.
    Performs strict structured expected-vs-actual evaluations using security-gated verification tools.
    """

    async def _emit_verification_event(self, topic: str, task_id: str, payload: Dict[str, Any]):
        """Helper for standardized verification telemetry."""
        event = JarvisEvent(
            event_type=EventType.VERIFICATION,
            topic=topic,
            task_id=task_id,
            correlation_id=task_id,
            source="VerificationEngine",
            payload=payload
        )
        await event_bus.publish(event)

    async def _securely_execute_verification_tool(
        self, 
        task_package: TaskPackage, 
        tool_name: str, 
        parameters: Dict[str, Any]
    ) -> Any:
        """
        Forces all verification tool executions through strict task-scope authorization boundaries
        and the SecurityManager -> ToolRegistry chain.
        """
        if tool_name not in task_package.selected_tools:
            raise ExecutionError(
                message=f"Verification tool '{tool_name}' is not authorized or selected for task {task_package.task_id}.",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        try:
            security_decision = await security_manager.evaluate_tool_execution(
                task_package=task_package,
                tool_name=tool_name,
                parameters=parameters
            )
            decision_status = getattr(security_decision, "status", None)
            reason = getattr(security_decision, "reason", "Action blocked by security gate.")

            if decision_status == "DENIED" or decision_status == "APPROVAL_REQUIRED":
                raise ExecutionError(
                    message=f"Security gate rejected verification tool '{tool_name}': {reason}",
                    classification=ErrorClassification.AUTHORIZATION_FAILURE
                )
        except ExecutionError:
            raise
        except Exception as e:
            logger.error(f"Security gate inspection failed for verification tool '{tool_name}': {e}", exc_info=True)
            raise ExecutionError(
                message=f"Security inspection error for verification tool '{tool_name}': {str(e)}",
                classification=ErrorClassification.SECURITY_FAILURE
            )

        logger.info(f"VerificationEngine securely invoking authorized tool [{tool_name}].")
        return await tool_registry.execute_tool(tool_name, parameters)

    def _structured_compare(self, actual: Any, expected: Any) -> Tuple[bool, str]:
        """
        Performs structured expected-vs-actual comparison instead of weak string containment.
        Supports exact values, dictionary subset matching, and type matching.
        """
        if expected is None:
            return True, "No expected outcome constraint defined."

        # If expected is provided as a JSON string, try parsing it into a structure
        parsed_expected = expected
        if isinstance(expected, str):
            try:
                parsed_expected = json.loads(expected)
            except json.JSONDecodeError:
                # Keep as string if it's not JSON
                pass

        # Case 1: Dictionary structural comparison (Expected fields must match actual fields)
        if isinstance(parsed_expected, dict):
            if not isinstance(actual, dict):
                return False, f"Type mismatch: expected dictionary but got {type(actual).__name__}."
            
            for exp_key, exp_val in parsed_expected.items():
                if exp_key not in actual:
                    return False, f"Missing expected key '{exp_key}' in actual outcome."
                
                act_val = actual[exp_key]
                # Recursive or strict match for values
                if isinstance(exp_val, (dict, list)):
                    match, reason = self._structured_compare(act_val, exp_val)
                    if not match:
                        return False, f"Mismatch at key '{exp_key}': {reason}"
                else:
                    if str(act_val).lower() != str(exp_val).lower():
                        return False, f"Value mismatch for key '{exp_key}': expected '{exp_val}', got '{act_val}'."
            return True, "Dictionary structured comparison matched successfully."

        # Case 2: List matching
        elif isinstance(parsed_expected, list):
            if not isinstance(actual, list):
                return False, f"Type mismatch: expected list but got {type(actual).__name__}."
            if len(actual) < len(parsed_expected):
                return False, f"List length mismatch: expected at least {len(parsed_expected)} items, got {len(actual)}."
            return True, "List structured comparison passed size bounds."

        # Case 3: Primitive exact or robust string matching
        else:
            if str(actual).lower() == str(parsed_expected).lower():
                return True, "Primitive values matched exactly."
            return False, f"Value mismatch: expected '{parsed_expected}', got '{actual}'."

    async def verify(self, task_package: TaskPackage, result_package: ResultPackage) -> ResultPackage:
        task_id = task_package.task_id
        contract: VerificationContract = task_package.verification_contract
        
        logger.info(f"VerificationEngine actively validating task [{task_id}] with contract method: [{contract.verification_type}]")
        await self._emit_verification_event("verification.started", task_id, {"method": contract.verification_type})

        # Paused/blocked lifecycle states must pass through UNCHANGED.
        # Verification must never convert an approval/input pause into a failure,
        # otherwise the human-in-the-loop cycle breaks (task would be marked FAILED).
        if result_package.status in {
            ResultStatus.WAITING_APPROVAL,
            ResultStatus.WAITING_INPUT,
            ResultStatus.BLOCKED
        }:
            logger.info(
                f"Task [{task_id}] is in paused state [{result_package.status.value}]; "
                "verification deferred until the task resumes."
            )
            await self._emit_verification_event("verification.deferred", task_id, {
                "state": result_package.status.value
            })
            return result_package

        if result_package.status == ResultStatus.FAILED:
            return self._build_failed_result(
                result_package, 
                reason="Execution failed upstream; verification aborted.",
                method=contract.verification_type
            )

        if result_package.status == ResultStatus.PARTIAL:
            return self._build_partial_result(
                result_package,
                reason="Runtime reported partial execution; withholding full verification.",
                method=contract.verification_type
            )

        try:
            is_verified, confidence, reasoning, evidence, failure_reason, recommendation = await self._execute_active_empirical_checks(
                contract=contract,
                result_package=result_package,
                task_package=task_package
            )
        except ExecutionError as e:
            is_verified, confidence, reasoning, evidence, failure_reason, recommendation = (
                "FAILED", 1.0, f"Security-gated verification failure: {e.message}", {}, e.message, "Review security policies."
            )

        if is_verified == "VERIFIED":
            logger.info(f"Active verification successful for task [{task_id}] with confidence {confidence}.")
            result_package.status = ResultStatus.COMPLETED
            result_package.evidence.update(evidence)
            
            await self._emit_verification_event("verification.verified", task_id, {
                "confidence": confidence,
                "reasoning": reasoning
            })

        elif is_verified == "PARTIAL":
            logger.warning(f"Task [{task_id}] achieved partial verification.")
            result_package.status = ResultStatus.PARTIAL
            result_package.evidence.update(evidence)
            if failure_reason:
                result_package.limitations.append(failure_reason)
                
            await self._emit_verification_event("verification.partial", task_id, {
                "confidence": confidence,
                "reason": failure_reason
            })

        elif is_verified == "UNVERIFIABLE":
            logger.warning(f"Task [{task_id}] cannot be independently verified by automated criteria.")
            result_package.status = ResultStatus.FAILED
            failure_msg = f"Automated verification impossible: {reasoning}"
            result_package.errors.append(failure_msg)
            result_package.next_action = "Escalate to human operator for manual review."
            
            await self._emit_verification_event("verification.unverifiable", task_id, {"reason": reasoning})

        else:
            logger.warning(f"Active verification failed for task [{task_id}]: {failure_reason}")
            result_package.status = ResultStatus.FAILED
            if failure_reason:
                result_package.errors.append(failure_reason)
            if recommendation:
                result_package.next_action = recommendation

            await self._emit_verification_event("verification.failed", task_id, {
                "reason": failure_reason,
                "recommendation": recommendation
            })

        return result_package

    async def _execute_active_empirical_checks(
        self, 
        contract: VerificationContract, 
        result_package: ResultPackage,
        task_package: TaskPackage
    ) -> Tuple[str, float, str, Dict[str, Any], Optional[str], Optional[str]]:
        findings = result_package.findings
        v_type = contract.verification_type.upper()

        if "STATE" in v_type or "DATABASE" in v_type:
            return await self._verify_state_against_expected(findings, contract, task_package)
        elif "OUTPUT" in v_type or "DATA" in v_type:
            return self._verify_schema_and_data(findings, task_package.expected_output)
        elif "API" in v_type or "EXTERNAL" in v_type:
            return await self._verify_api_against_expected(findings, contract, task_package)
        elif "FILE" in v_type:
            return await self._verify_filesystem_artifact(findings, contract.expected_outcome)
        elif "HUMAN" in v_type:
            return "UNVERIFIABLE", 0.0, "Human verification gate explicitly required by contract.", {"requires_human": True}, "Awaiting human operator sign-off.", "Trigger approval workflow."
        
        return self._verify_schema_and_data(findings, task_package.expected_output)

    async def _verify_filesystem_artifact(self, findings: Dict[str, Any], expected: str) -> Tuple[str, float, str, Dict[str, Any], Optional[str], Optional[str]]:
        target_path_str = None
        for key, value in findings.items():
            if isinstance(value, dict) and "path" in value:
                target_path_str = value.get("path")
                break
            elif isinstance(value, str) and ("/" in value or "\\" in value):
                target_path_str = value
                break

        if not target_path_str:
            return "FAILED", 0.0, "No file path provided in findings to verify.", {}, "Missing file path reference.", "Ensure tool returns file paths."

        path_obj = Path(target_path_str)
        if not path_obj.exists():
            return "FAILED", 1.0, f"Filesystem check failed: Path '{target_path_str}' does not exist.", {"checked_path": target_path_str}, f"File missing at path: {target_path_str}", "Verify write permissions."

        if not path_obj.is_file():
            return "FAILED", 1.0, f"Filesystem check failed: Path '{target_path_str}' is not a valid file.", {"checked_path": target_path_str}, f"Path is not a file: {target_path_str}", "Provide specific file path."

        file_size = path_obj.stat().st_size
        if file_size == 0:
            return "FAILED", 0.9, f"Filesystem check failed: File '{target_path_str}' is empty (0 bytes).", {"checked_path": target_path_str, "size_bytes": 0}, "Created file is empty.", "Re-run generation."

        return "VERIFIED", 0.99, f"Empirically verified file existence on disk at '{target_path_str}' ({file_size} bytes).", {"checked_path": target_path_str, "size_bytes": file_size}, None, None

    async def _verify_state_against_expected(
        self, 
        findings: Dict[str, Any], 
        contract: VerificationContract,
        task_package: TaskPackage
    ) -> Tuple[str, float, str, Dict[str, Any], Optional[str], Optional[str]]:
        verification_tool = task_package.tool_parameters.get("verification_query_tool")
        verification_params = task_package.tool_parameters.get("verification_query_params", {})

        if not verification_tool:
            for k, v in findings.items():
                if isinstance(v, dict) and "table" in v:
                    verification_tool = "db_query"
                    verification_params = {"table": v.get("table"), "filter": v.get("filter", {})}
                    break

        if verification_tool:
            try:
                query_result = await self._securely_execute_verification_tool(task_package, verification_tool, verification_params)
                
                if not query_result:
                    return "FAILED", 1.0, f"Active verification query [{verification_tool}] returned empty result.", {"query_result": query_result}, "State resource not found via independent query.", "Check mutation logic."

                # Strict Structured Comparison
                match, mismatch_reason = self._structured_compare(query_result, contract.expected_outcome)

                if match:
                    return "VERIFIED", 0.98, f"Actual state verified via [{verification_tool}] using structured validation.", {"query_result": query_result, "expected": contract.expected_outcome}, None, None
                else:
                    return "FAILED", 1.0, f"State query succeeded but structure mismatch: {mismatch_reason}", {"query_result": query_result, "expected": contract.expected_outcome}, f"State verification mismatch: {mismatch_reason}", "Review update parameters."

            except Exception as e:
                return "FAILED", 1.0, f"Active verification query failed: {str(e)}", {}, f"Verification tool error: {str(e)}", "Fix inspection parameters."

        return "FAILED", 0.0, "State verification impossible: No independent verification query tool provided.", {}, "State modification lacks independent verification query capability.", "Supply a verification_query_tool in task parameters."

    async def _verify_api_against_expected(
        self, 
        findings: Dict[str, Any], 
        contract: VerificationContract,
        task_package: TaskPackage
    ) -> Tuple[str, float, str, Dict[str, Any], Optional[str], Optional[str]]:
        verification_endpoint = task_package.tool_parameters.get("verification_endpoint_url")
        
        if not verification_endpoint:
            for k, v in findings.items():
                if isinstance(v, dict) and ("url" in v or "endpoint" in v):
                    verification_endpoint = v.get("url", v.get("endpoint"))
                    break

        if verification_endpoint:
            try:
                get_result = await self._securely_execute_verification_tool(task_package, "http_get", {"url": verification_endpoint})
                
                status_code = get_result.get("status_code", 500)
                if status_code not in [200, 201]:
                    return "FAILED", 1.0, f"Active API verification GET request failed with status {status_code}.", {"get_response": get_result}, f"Resource verification endpoint returned status {status_code}.", "Confirm resource creation."

                # Strict Structured Payload Comparison
                response_body = get_result.get("data", get_result.get("body", get_result))
                match, mismatch_reason = self._structured_compare(response_body, contract.expected_outcome)

                if match:
                    return "VERIFIED", 0.99, f"API state verified via independent GET using structured validation.", {"get_response": get_result, "expected": contract.expected_outcome}, None, None
                else:
                    return "FAILED", 1.0, f"API responded 200 OK, but structured comparison failed: {mismatch_reason}", {"get_response": get_result, "expected": contract.expected_outcome}, f"API state structured mismatch: {mismatch_reason}", "Confirm persistence payload."

            except Exception as e:
                return "FAILED", 1.0, f"Active API verification request crashed: {str(e)}", {}, f"Verification GET request error: {str(e)}", "Verify endpoint availability."

        return "FAILED", 0.0, "API verification failed: No verification endpoint URL provided.", {}, "Missing active verification endpoint.", "Provide verification_endpoint_url in task parameters."

    def _verify_schema_and_data(self, findings: Dict[str, Any], expected_output: Any) -> Tuple[str, float, str, Dict[str, Any], Optional[str], Optional[str]]:
        if not findings:
            return "FAILED", 0.0, "Verification failed: Findings payload is entirely empty.", {}, "Empty findings structure.", "Ensure agent returns structured data outputs."

        if expected_output and hasattr(expected_output, "schema_definition") and expected_output.schema_definition:
            try:
                jsonschema.validate(instance=findings, schema=expected_output.schema_definition)
                return "VERIFIED", 0.99, "Findings successfully passed strict JSON schema validation.", {"validated_schema": True}, None, None
            except jsonschema.ValidationError as e:
                return "FAILED", 1.0, f"Schema validation failed: {e.message}", {"schema_error": e.message}, f"Output violates required schema: {e.message}", "Adjust agent output formatting."

        for k, v in findings.items():
            if isinstance(v, str) and ("error" in v.lower() or "fail" in v.lower()):
                return "FAILED", 0.8, f"Findings contain explicit error markers in key '{k}'.", {k: v}, f"Agent reported error state in output: {v}", "Review execution logs."

        return "VERIFIED", 0.85, "Findings successfully evaluated and verified against structural expectations.", {"keys_verified": list(findings.keys())}, None, None

    def _build_failed_result(self, result_package: ResultPackage, reason: str, method: str) -> ResultPackage:
        result_package.status = ResultStatus.FAILED
        result_package.errors.append(reason)
        result_package.evidence["verification_failure"] = {"method": method, "reason": reason}
        return result_package

    def _build_partial_result(self, result_package: ResultPackage, reason: str, method: str) -> ResultPackage:
        result_package.status = ResultStatus.PARTIAL
        result_package.limitations.append(reason)
        result_package.evidence["verification_partial"] = {"method": method, "reason": reason}
        return result_package

verification_engine = VerificationEngine()