import time

import jwt
import pytest

from webmind.core.config import AccessRule, AuthenticationConfig, JWTConfig, SecurityConfig, config_from_dict
from webmind.core.errors import AuthenticationError
from webmind.security.identity import IdentityVerifier, issue_token
from webmind.security.policy import PolicyEngine

pytestmark = pytest.mark.security
SECRET = "s" * 48


def verifier(**auth) -> IdentityVerifier:
    cfg = config_from_dict(
        {
            "security": {
                "authentication": {"mode": "host_application", "jwt": {"secret": SECRET}, **auth},
                "roles": {
                    "public": {"capabilities": ["chat"]},
                    "employee": {"capabilities": ["records:read"], "inherits": ["public"]},
                    "manager": {"capabilities": ["export"], "inherits": ["employee"]},
                },
            }
        }
    )
    return IdentityVerifier(cfg.security.authentication, PolicyEngine(cfg.security), default_tenant="default")


def test_valid_token_produces_principal_with_inherited_capabilities():
    v = verifier()
    p = v.verify(issue_token(SECRET, user_id="u1", roles=["manager"], attributes={"department": "eng"}))
    assert p.authenticated and p.user_id == "u1" and p.tenant_id == "default"
    assert {"chat", "records:read", "export"} <= p.capabilities
    assert p.attributes == {"department": "eng"}


def test_missing_token_is_anonymous_only_when_allowed():
    assert verifier().verify(None).authenticated is False
    with pytest.raises(AuthenticationError):
        verifier(allow_anonymous=False).verify(None)


@pytest.mark.parametrize(
    "make",
    [
        lambda: issue_token("w" * 48, user_id="u"),  # wrong key
        lambda: issue_token(SECRET, user_id="u", ttl_seconds=-120),  # expired
        lambda: jwt.encode({"sub": "u", "iat": int(time.time()), "exp": int(time.time()) + 60}, None, algorithm="none"),
        lambda: jwt.encode({"sub": "u", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256"),  # no iat
        lambda: issue_token(SECRET, user_id="u", ttl_seconds=86400),  # lifetime too long
        lambda: jwt.encode(
            {"sub": "u", "iat": int(time.time()), "exp": int(time.time()) + 60, "roles": {"a": 1}}, SECRET
        ),
        lambda: jwt.encode(
            {"sub": "u", "iat": int(time.time()), "exp": int(time.time()) + 60, "tenant": "../evil"}, SECRET
        ),
        lambda: "not-a-jwt",
    ],
)
def test_invalid_tokens_are_rejected_never_downgraded(make):
    with pytest.raises(AuthenticationError):
        verifier().verify(make())


def test_hs_rs_algorithm_confusion_is_rejected_by_config():
    with pytest.raises(Exception, match="mix"):
        AuthenticationConfig(
            mode="host_application", jwt=JWTConfig(algorithms=["HS256", "RS256"], secret=SECRET, public_key="x")
        )


def test_issuer_and_audience_enforced():
    v = verifier(jwt={"secret": SECRET, "issuer": "host-app", "audience": "webmind"})
    good = issue_token(SECRET, user_id="u", issuer="host-app", audience="webmind")
    assert v.verify(good).user_id == "u"
    with pytest.raises(AuthenticationError):
        v.verify(issue_token(SECRET, user_id="u", issuer="other", audience="webmind"))
    with pytest.raises(AuthenticationError):
        v.verify(issue_token(SECRET, user_id="u", issuer="host-app"))


def test_unknown_roles_grant_nothing_and_cycles_are_safe():
    sec = SecurityConfig(
        roles={
            "a": {"capabilities": ["x"], "inherits": ["b"]},
            "b": {"capabilities": ["y"], "inherits": ["a"]},
            "public": {"capabilities": []},
        }
    )
    pe = PolicyEngine(sec)
    assert pe.capabilities_for(["a"]) == {"x", "y"}
    assert pe.capabilities_for(["superuser"]) == frozenset()


def _p(pe, authenticated=True, roles=(), tenant="t1", **attrs):
    return pe.build_principal(
        tenant_id=tenant,
        user_id="u" if authenticated else None,
        roles=roles,
        attributes=attrs,
        authenticated=authenticated,
    )


def test_access_rules_rbac_abac_and_tenant():
    pe = PolicyEngine(SecurityConfig())
    public = AccessRule(visibility="public")
    staff = AccessRule(visibility="restricted", roles=["employee"])
    eng = AccessRule(visibility="restricted", attributes={"department": ["engineering", "research"]})
    anyone_signed_in = AccessRule(visibility="restricted")

    anon = _p(pe, authenticated=False)
    emp = _p(pe, roles=["employee"], department="finance")
    engineer = _p(pe, roles=["intern"], department="engineering")

    assert pe.can_access(anon, public, tenant_id="t1")
    assert not pe.can_access(anon, public, tenant_id="t2")  # tenant isolation applies to public data too
    assert not pe.can_access(anon, staff, tenant_id="t1")
    assert not pe.can_access(anon, anyone_signed_in, tenant_id="t1")
    assert pe.can_access(emp, staff, tenant_id="t1")
    assert not pe.can_access(emp, eng, tenant_id="t1")
    assert pe.can_access(engineer, eng, tenant_id="t1")
    assert pe.can_access(engineer, anyone_signed_in, tenant_id="t1")
    assert not pe.can_access(emp, None, tenant_id="t1")  # deny by default
