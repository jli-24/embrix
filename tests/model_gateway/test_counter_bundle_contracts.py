"""Offline §14 public contract and pre-producer legacy-vector checks."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.contracts import ModelPolicy, ModelRequest
from embedded_copilot.model_gateway.governance import (
    BoundModelIdentity,
    EngineeringStage,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    ConditionalGenerationCeilingV1,
    CounterAdmissionV1,
    CounterObservationV1,
    InvocationBudgetEvidenceV1,
    InvocationMode,
    ModelExecutionSnapshot,
    ModelInvocationAttempt,
    ModelInvocationGenerationOptions,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    SingleInvocationBudgetEnvelopeV1,
    SingleInvocationBudgetEnvelopeV2,
)
from embedded_copilot.model_gateway.profiles import ProtocolFamily
from tests.model_gateway.test_invocation_service import _request

_NOW = datetime(2026, 10, 15, tzinfo=UTC)
_FP = "sha256:" + "a" * 64
_COUNT_REQUEST = (
    b'{"model":"gpt-5.6-luna","instructions":"system","input":"task",'
    b'"text":{"format":{"type":"json_object"}}}'
)
_LEGACY_B85 = (
    "c-rk-S##Vr5`Irr{)20tGY&uy0O2>qp6se3ODaiDb~lw`;gq=MkQ0(4TbuHK-{uX@p^*>iVOOkjO2cjdjYc0|H<-V^|L(<$*(y&H"
    "eK&jc;;;H={h~;gvbc*^MShzYTL5X6JC`Pzo&C`9IGLtO>h}Xv30%Mb(UX_Cv1y#irS%5SQ+q98rJkv>%atu;wJvHd^b!rD<;OT*"
    "SF3duSNVIJ)pKJMgu~)Vc5Mp~Gpp+9%NrR`_DTj!D1;=_HWGwOVO-#%C}L6|WH2U3FlR!FC>GpU7io($35cYOvrwv8J^QC-jaj~`"
    "lDt{>uZIY`Q_3pO(-_uCvZSAER8UzJYh6!f`kj{g#x5l=W$QHcL`kyST+4cfxUhe&ZCQCzb#LOt)QaeS&GDdOIGul6-P5~Y9=)vO"
    "we6^XKe~AH+tI}$zFeFvUSA!boepfC4E4#@+*e1JzYKJ_u5R)osqR2TXA7HYI||t-FOyj>Mf+nE;;@*&VY6$nAPYx+9apk^-_Heq"
    "PWT#Hua!M^@|BObn$GjWBw4GOM_+A~%B)ur{5jQNP1AV>%6)=;7xp%>AD&>xH?r)_v-x|v=Z5+;YWHle(fyX;<xO;`?O>lb#pf4i"
    "=VzBkCp$-}Br~;fKhk#E6+aEJsoqX?w`yGZ=i`gFwWwp%kauY=&D4cqM9u7yV);Q9_C@nzw}Uoz__6ZGH_p<$?ex?AnrlV1WW)r<"
    "QspGV$fAI-2n!dnfH`5g#8@z3Ba~Un1-6z69$|>y&WTZkhs2T1SOno&uG4DXzMS&aDl)dzMbadLdp2KZ0lUm0W!gFyw$X2h^$q>H"
    "kgJ>h<o8+rA+-i#@m`iC$eBR&mFa#SP%30vU6ng2!yxpMZfNbc-v5pXZ{a{)(uICV7S+0*diL)0*Tu#0&&P{56D91n-7w;C#0c>S"
    "ai<*$k>pfF(uCYl_zxqTSmv<ep$VO}PN66=JfMNZoN+trSNJqqTX|PPLXMNH&7<A%yesXCa=lumi8U{JCB4YA^zP*(XxF182)Usq"
    "IK;AqeAv7mx8>Pz9s2ULahi1r!03kwsSCf`hB9(}>i_gsl~pD6&C+hm12C+U=8uW^a4MxAYy4MpwFWctOc{1nNh7NsT8xC<$K^t5"
    "1SN_KB1IH{Z>kW(aX4mJqy!R_gLg6oUdzBo2^KWMfuWi+tdVh>p{7p8NsV)~TuGIr0EVV}8bAzKVNoOvyzm5_UE8b=l3TFs9$<D4"
    "G&=y!9tO|+Xj({ZcSXI9vIm_tk+!H4cDd>^`}Bl#UKBQ!oqc;Nd-xE{2Q=V=!tPBca$K86I|1nhZfzvCU^XeFpiaOZrswdlC(Sm8"
    "bY8V5PccEY*32?VC`L5UG{6?Dg9r<z6fq)#$Pvx4vXWaVg(CtYzQbTTj2XSTTw%e@Zhc#C3G;8Z&>@6;2w~rkHM!Iwi8v2K#kf+;"
    "8U&J19UAOhsDv@n*+5#0O#otoFR6$cM<LIFKap?*>X}@XH~G}MwhlO$go(`Ru57qXI)+{@@|DkLVfTj>88xF#VJ!GuQ$B9MhrZAp"
    "P9l|elk8(Nv&xepwf8^g+ngHw8r&{{R0{P<6G4YJueRCSViwkSN!stDdDl)o?}Po0K`nF7FmLy@BcFMv)eVvF?R<Rer!Qal@nG8l"
    "18qLge2CU5XltCOtF2LOdmt|Jb<qHX+2z&I-xl%t*~#(ie{S>b_5=Kt&z6zwFSvrQd}fCdB;iaN8dxfr#32A0j1i{BMnY1JA*X=1"
    "6AH;Qq>6JMsnBuDIo1RjV>Q}LkRz;mefIYJWN|g3sus2tOK^$7{QH2T^K~PZpDLg0^yfPLxlaEds?)D!Wk1L}z|Ltpcs$;u0!mr|"
    "hP837YPe+UjRAnQ9zM;0qX0+<k$z|7D6F<n=kD2-b5W=yi4;H=!B{{EqY43{VH64jEF&9ADyT&yk^z^J859{RLP0|vIO%5hcW#j~"
    "A%dzO3J>fUa!jef38w(U!J^hG0CZeCp#mX8r<mh}P)4cc2w4*tzjNDd#Dl|ID2YA8;Qb3hY|>DqjTL{BMHZJ=$8V4P%ybABKYdN~"
    "-1C0UrO&zaIhTH6&-)=|j#qs2G2!Rqe=Od_Z_nN=PIhn=R+ep@)lRpcdso$apyqvO#rs+H?iW<MhilzWUOh9ZbA1S+C0K)jgcFbY"
    "^E%x=E5zRXFxT!%d5U@t^(K?7ZvHiG_krkzct~ySuW>-#vmGE7@}b&fu;1F~@5u)#ru~PpKs-OXyqu7Xtz?o;@}S>W*tLkfg%OXx"
    "%&7cFTx4*78!OZ5<`Gx=bDdZ6>*n7u6c}IBUN4)N7odR6F`8p~h3G33zM}YL5V44`e|gP-<tnuY#t2R*8pi-o0?)XMmu1r&=K<!T"
    "c__wVC_KzG?Ji8Aa9>~y)816oYP=5$5E?zIT&In6m+xM`UW|&F*`mk`aEz_~{@%_8dXg$=m}gzz{Y=7d>-@tFg4bQ${5pmBHwzU6"
    "oPk2UFf5@GG4aN%8|b#?i9Z0YwEI}voYH{8-3*>W>+ROzEibDrHc&MLE00X=95GLnby1_*yXI!?(!1%KLGVbMd)`!*+sik4^b#pR"
    "fM1W_EH2`U#rfIAm49cr?+S`UBGQUSHVT6XNlC2W$_k|-X@s$aL)ocAP6H|<t_cbp69h4#Wf)o(Y-XKDDr&u|bGtpizd8Rzt@&?N"
    "2mL?8lYMRYFKxnqNeljg$n;6t?@!izf6Uf<=dwd<=N^~E<GDS&MRafBf6yyG?|Of>w=d9A#wNvXg({{ZqXH=+>;xm+8HAM(7E~q#"
    "HleYG5KOTMG*ZkF5`nr=D4llPVaz<k9(xr{U^x&~R_kJwmkrm&;^-u9ZTbix!}-<G>FcpB+mnTwyZt(E#c-~W#_GT{LoYF!Fm#Bb"
    "^p7&jt5~|K{jAl8P;7uuI0`)owOeywWn2l!9W>&U608(M!f-^5U@p`e5sh&aX(vI$m@`Y6W6o(V2_uma6H^@Pm&2xbpwWXg<S7gJ"
    "j1Z2HAtHhU1GpCuEF_ki1R*8FhK_~~IKeZRcFI654YcyIhy7^vx@`h>xh;MqpY-%x1E&$CHS_yzVyECi0YyPzEr*Rj`c{Y-WgS&G"
    "f(ZkKF;SM<z&OXT3=j$H&r_TT0{5KK?oDY=gG!z>a~64(>pYE}TqbD~Le9<?r$@&CMK90KPA?ai9&e(%Zx_w@Nl>rw-~Ey>FCg9|"
    "{{j2ypsx19f5UQgauWY^d~$O1)5&7E?*C(>0Bj17_<fR@9wjeM<9FxhfRukadw2R~Htj()U_VwcsOHDPWSYicYt>bEet!CM4T8GF"
    "nt8`H+Z^R<vme#b=Eayyx{11e_<`GxZ)YowhCVxB`^8zDCd;JS_%eTR@|7&0zVD{q{=W`J+jBW!ulf_cC)|4-`{TOet;;Q`>d;Ud"
    "!bVlQi?7|q24j-xbZu-I5B1{)L$~+OR(J0?yA04JAHn!4StQPF4cKLSHO=~#*TUZIWbo}2y$4{dv&L>_wLe~+{jxaS@W)2dDf&S5"
    "gE#H)Fk@Tv!?~C(=4pd;><t{|D*->idFZA^b$WI>LMmz62nQk9Sulv<mhYF-v)@kReY_3YYU0miGh4^{*#<PG{I3{#$S*BLT1X8#"
    "mZFBb$Z|oQ)S<J|)vX4kxHO?)oDl`+7SJx>;7Ch{qJW9nRweLAuyAT*jn3Dr6~ro6$l*h0@*{vCcs5OK4?AW*V#)%#_f)rsj8nJ$"
    "tUYW26`P_w(v5fnm%sn+(|-XY>C-&"
)
_LEGACY_RAW_SHA256 = "88ce59b7d9e8e4bc13ea9eaf2031c334f1e484be35b4e472a8e961a837d50f7b"


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _identity() -> BoundModelIdentity:
    return BoundModelIdentity(
        provider_id="openai",
        endpoint_id="endpoint-openai",
        credential_ref="credential:offline",
        model_profile_id="luna-profile",
        model_id="gpt-5.6-luna",
    )


def _envelope() -> SingleInvocationBudgetEnvelopeV2:
    return SingleInvocationBudgetEnvelopeV2(
        envelope_id="offline-bundle",
        tenant_id="tenant-1",
        project_id="project-1",
        conversation_id="conversation-1",
        task_id="task-1",
        task_authority_fingerprint=_FP,
        engineering_stage=EngineeringStage.HARDWARE_SELECTION,
        not_after=_NOW + timedelta(minutes=10),
    )


def _ceiling() -> ConditionalGenerationCeilingV1:
    return ConditionalGenerationCeilingV1(
        unit="PER_1M_TOKENS",
        input_price_decimal="0.01",
        output_price_decimal="0.02",
        cache_semantics="NONE",
        additional_fees_state="KNOWN_NONE",
        additional_cost_bound_decimal="0",
        pricing_identity="offline-tariff",
        pricing_revision="revision-1",
        pricing_source_sha256=_FP,
        pricing_conditions=(("region", "STANDARD"), ("service_tier", "DEFAULT")),
        pricing_not_after=_NOW + timedelta(minutes=10),
        output_not_after=_NOW + timedelta(minutes=10),
        transport_not_after=_NOW + timedelta(minutes=10),
        cap_evidence_fingerprint=_FP,
        transport_evidence_fingerprint=_FP,
        grant_evidence_fingerprint=_FP,
        covers_all_billable_input=True,
        covers_all_billable_output=True,
        includes_reasoning=True,
    )


def _admission() -> CounterAdmissionV1:
    return CounterAdmissionV1(
        envelope_fingerprint=_envelope().fingerprint,
        request_fingerprint=_FP,
        execution_snapshot_fingerprint=_FP,
        task_authority_fingerprint=_FP,
        policy_fingerprint=_FP,
        selected_model=_identity(),
        connection_id="connection-1",
        connection_revision=_FP,
        credential_revision="credential-1",
        endpoint_revision="endpoint-1",
        protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        final_generation_bytes_sha256=_FP,
        count_request_base64=base64.b64encode(_COUNT_REQUEST).decode("ascii"),
        count_request_sha256=_sha(_COUNT_REQUEST),
        counter_wire_target_sha256=_FP,
        generation_wire_target_sha256=_FP,
        counter_fee_identity="offline-counter-fee",
        counter_fee_revision="revision-1",
        counter_fee_source_sha256=_FP,
        counter_fee_upper_decimal="0.001",
        counter_fee_evidence_fingerprint=_FP,
        counter_fee_not_after=_NOW + timedelta(minutes=10),
        generation_ceiling=_ceiling(),
        generation_cost_upper_fraction="1/100",
        combined_cost_upper_fraction="11/1000",
        not_after=_NOW + timedelta(minutes=10),
    )


def _observation(
    *, status=500, body=b'{"error":"offline"}', kind="HTTP_ERROR", count=None
):
    admission = _admission()
    return CounterObservationV1(
        envelope_id="offline-bundle",
        project_id="project-1",
        request_id="request-1",
        counter_admission_fingerprint=admission.fingerprint,
        execution_snapshot_fingerprint=_FP,
        task_authority_fingerprint=_FP,
        selected_model=_identity(),
        connection_id="connection-1",
        connection_revision=_FP,
        credential_revision="credential-1",
        endpoint_revision="endpoint-1",
        final_generation_bytes_sha256=_FP,
        count_request_sha256=_sha(_COUNT_REQUEST),
        counter_wire_target_sha256=_FP,
        count_request_base64=admission.count_request_base64,
        raw_response_base64=base64.b64encode(body).decode("ascii"),
        response_sha256=_sha(body),
        http_status=status,
        observation_status=kind,
        input_tokens=count,
        current_witness_fingerprints=(_FP,),
        observed_at=_NOW,
        not_after=_NOW + timedelta(minutes=10),
    )


def test_bundle_envelope_exact_two_request_roles_and_limits():
    envelope = _envelope()
    assert (
        envelope.max_requests,
        envelope.max_counter_requests,
        envelope.max_generation_requests,
    ) == (2, 1, 1)
    assert (envelope.input_admission_limit, envelope.total_output_limit) == (8192, 2048)
    assert envelope.engineering_stage is EngineeringStage.HARDWARE_SELECTION
    assert (
        SingleInvocationBudgetEnvelopeV2.model_validate_json(envelope.model_dump_json())
        == envelope
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_requests", True),
        ("max_requests", 1),
        ("max_counter_requests", 2),
        ("max_generation_requests", False),
        ("input_admission_limit", 8193),
        ("input_admission_limit", 8192.0),
        ("total_output_limit", 2049),
        ("engineering_stage", EngineeringStage.SCHEMATIC_DESIGN),
    ],
)
def test_envelope_rejects_wrong_roles_limits_and_bool_numbers(field, value):
    payload = {
        **_envelope().model_dump(mode="python", exclude={"fingerprint"}),
        field: value,
    }
    with pytest.raises(ValidationError):
        SingleInvocationBudgetEnvelopeV2.model_validate(payload)


def test_conditional_ceiling_and_native_luna_admission_roundtrip():
    admission = _admission()
    assert admission.selected_model.model_id == "gpt-5.6-luna"
    assert admission.protocol_family is ProtocolFamily.OPENAI_RESPONSES
    assert admission.input_admission_limit == 8192
    assert admission.total_output_limit == 2048
    assert base64.b64decode(admission.count_request_base64) == _COUNT_REQUEST
    assert admission.count_request_sha256 == _sha(_COUNT_REQUEST)
    assert admission.generation_ceiling.covers_all_billable_output is True
    assert (
        CounterAdmissionV1.model_validate_json(admission.model_dump_json()) == admission
    )


@pytest.mark.parametrize(
    "change",
    [
        {"count_request_sha256": _FP},
        {"count_request_base64": "not base64!"},
        {"input_admission_limit": True},
        {"total_output_limit": 2049},
        {"protocol_family": ProtocolFamily.OPENAI_CHAT},
        {
            "selected_model": BoundModelIdentity(
                provider_id="openai",
                endpoint_id="endpoint-openai",
                credential_ref="credential:offline",
                model_profile_id="other",
                model_id="gpt-5.6-sol",
            )
        },
    ],
)
def test_admission_rejects_wrong_original_or_native_profile(change):
    payload = {
        **_admission().model_dump(mode="python", exclude={"fingerprint"}),
        **change,
    }
    with pytest.raises(ValidationError):
        CounterAdmissionV1.model_validate(payload)


def test_raw_http_error_and_overlimit_count_are_preserved():
    failed = _observation()
    assert failed.http_status == 500
    assert failed.observation_status == "HTTP_ERROR"
    assert failed.input_tokens is None
    assert base64.b64decode(failed.raw_response_base64) == b'{"error":"offline"}'
    counted_body = b'{"object":"response.input_tokens","input_tokens":8193}'
    counted = _observation(status=200, body=counted_body, kind="COUNTED", count=8193)
    assert counted.input_tokens == 8193
    assert base64.b64decode(counted.raw_response_base64) == counted_body
    assert (
        CounterObservationV1.model_validate_json(counted.model_dump_json()) == counted
    )


@pytest.mark.parametrize(
    "change",
    [
        {"http_status": True},
        {"input_tokens": True},
        {"response_sha256": _FP},
        {"raw_response_base64": "not base64!"},
        {"observation_status": "COUNTED", "http_status": 500, "input_tokens": 1},
        {"observation_status": "HTTP_ERROR", "http_status": 200},
    ],
)
def test_observation_rejects_invalid_types_or_original_hashes(change):
    payload = {
        **_observation().model_dump(mode="python", exclude={"fingerprint"}),
        **change,
    }
    with pytest.raises(ValidationError):
        CounterObservationV1.model_validate(payload)


def _tuples(value):
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuples(item) for key, item in value.items()}
    return value


def _legacy_vectors():
    raw = zlib.decompress(base64.b85decode(_LEGACY_B85))
    assert hashlib.sha256(raw).hexdigest() == _LEGACY_RAW_SHA256
    return json.loads(raw)


def test_exact_legacy_json_dumps_and_fingerprints_from_preproducer_vectors():
    # Compressed copy of the actual eight-vector pre-producer artifact; no fixture
    # is regenerated from current code when comparing historical bytes.
    vectors = _legacy_vectors()
    classes = {
        "policy": ModelPolicy,
        "options": ModelInvocationGenerationOptions,
        "invocation_request": ModelInvocationRequest,
        "attempt": ModelInvocationAttempt,
        "receipt": ModelInvocationReceipt,
        "snapshot": ModelExecutionSnapshot,
        "envelope": SingleInvocationBudgetEnvelopeV1,
        "evidence": InvocationBudgetEvidenceV1,
    }
    assert set(vectors) == set(classes)
    for name, before in vectors.items():
        if name in {"policy", "options", "invocation_request"}:
            input_value = _tuples(before)
            if name == "invocation_request":
                input_value["stage_id"] = EngineeringStage(input_value["stage_id"])
                input_value["invocation_mode"] = InvocationMode(
                    input_value["invocation_mode"]
                )
            current = classes[name].model_validate(input_value)
        else:
            current = classes[name].model_validate_json(json.dumps(before))
        assert current.model_dump(mode="json") == before, name
        if "fingerprint" in before:
            assert current.fingerprint == before["fingerprint"], name


def _terminal_bundle_payload(kind):
    payload = _legacy_vectors()[kind].copy()
    payload["fingerprint"] = "sha256:" + "0" * 64
    payload["attempt_schema_version" if kind == "attempt" else "schema_version"] = (
        4 if kind == "attempt" else 7
    )
    payload.update(
        budget_phase="TERMINAL",
        counter_admission_fingerprint=_FP,
        counter_observation_fingerprint=_FP,
        counter_status="COUNTED",
        counter_transport_send_count=1,
        counter_response_observed=True,
        generation_transport_send_count=1,
        total_external_request_count=2,
        counter_http_status=200,
        counter_input_tokens=8192,
    )
    return payload


@pytest.mark.parametrize(
    "kind,contract",
    [
        ("attempt", ModelInvocationAttempt),
        ("receipt", ModelInvocationReceipt),
    ],
)
def test_terminal_bundle_roles_roundtrip_with_two_external_operations(kind, contract):
    current = contract.model_validate_json(json.dumps(_terminal_bundle_payload(kind)))
    assert current.counter_transport_send_count == 1
    assert current.generation_transport_send_count == 1
    assert current.total_external_request_count == 2
    assert current.counter_input_tokens == 8192
    assert contract.model_validate_json(current.model_dump_json()) == current
    assert current.fingerprint != _legacy_vectors()[kind]["fingerprint"]


@pytest.mark.parametrize(
    "kind,contract",
    [
        ("attempt", ModelInvocationAttempt),
        ("receipt", ModelInvocationReceipt),
    ],
)
@pytest.mark.parametrize(
    "change",
    [
        {"counter_input_tokens": 8193},
        {"counter_input_tokens": True},
        {"counter_observation_fingerprint": None},
        {"counter_transport_send_count": None},
        {"counter_response_observed": False},
        {"generation_transport_send_count": None},
        {"total_external_request_count": 1},
        {"counter_status": "HTTP_ERROR"},
        {"counter_http_status": 500},
    ],
)
def test_bundle_roles_reject_missing_invalid_or_inconsistent_counter_state(
    kind, contract, change
):
    payload = {**_terminal_bundle_payload(kind), **change}
    with pytest.raises(ValidationError):
        contract.model_validate_json(json.dumps(payload))


def test_bundle_attempt_counter_intent_cannot_claim_a_counter_response():
    payload = _terminal_bundle_payload("attempt")
    payload.update(
        status="DISPATCH_INTENT",
        budget_phase="COUNTER_INTENT",
        counter_status="DISPATCH_INTENT",
        counter_observation_fingerprint=None,
        counter_transport_send_count=None,
        counter_response_observed=False,
        generation_transport_send_count=0,
        total_external_request_count=None,
        counter_http_status=None,
        counter_input_tokens=None,
        receipt_fingerprint=None,
        gateway_response_observed=False,
    )
    intent = ModelInvocationAttempt.model_validate_json(json.dumps(payload))
    assert intent.total_external_request_count is None
    assert intent.counter_transport_send_count is None
    for invalid in (
        {"counter_transport_send_count": 1, "total_external_request_count": 1},
        {"counter_response_observed": True},
        {"generation_transport_send_count": 1, "total_external_request_count": 1},
    ):
        with pytest.raises(ValidationError):
            ModelInvocationAttempt.model_validate_json(
                json.dumps({**payload, **invalid})
            )


def test_explicit_default_tier_roundtrip_and_responses_payload_only():
    legacy = ModelPolicy(primary_provider="openai", model_name="gpt-5.6-luna")
    explicit = ModelPolicy(
        primary_provider="openai",
        model_name="gpt-5.6-luna",
        service_tier="default",
        max_output_tokens=2048,
    )
    assert "service_tier" not in legacy.model_dump(mode="json")
    assert ModelPolicy.model_validate(explicit.model_dump(mode="python")) == explicit
    options = ModelInvocationGenerationOptions(service_tier="default")
    assert (
        ModelInvocationGenerationOptions.model_validate(
            options.model_dump(mode="python")
        )
        == options
    )
    assert options.model_dump(mode="json")["service_tier"] == "default"
    original = _request()
    with_tier = ModelRequest(
        request_id=original.request_id,
        project_id=original.project_id,
        task_type=original.task_type,
        task=original.task,
        engineering_context=original.engineering_context,
        system_instruction=original.system_instruction,
        model_policy=explicit,
        messages=original.messages,
    )
    adapter = ProtocolHTTPProviderAdapter(
        provider="openai",
        endpoint_id="endpoint-openai",
        base_url="https://api.openai.com/v1",
        protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        api_key="offline-placeholder",
    )
    assert adapter._payload(with_tier)["service_tier"] == "default"
    assert b'"service_tier":"default"' in adapter._prepare_transport_bytes(with_tier)
    old_bytes = adapter._prepare_transport_bytes(original)
    assert old_bytes == (
        b'{"model":"firmware-model","instructions":"Use supplied evidence only.",'
        b'"input":[{"role":"user","content":"firmware task"}],'
        b'"max_output_tokens":1024}'
    )
