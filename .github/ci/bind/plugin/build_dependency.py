"""Resolve the plugin's ordinary BIND dependency independently of RP releases."""
import base64
import hashlib
from bind.runtime import bind920_profile
from shared import release_channel, target_pkg


def from_recipe(upstream, creator_target, makefile, distinfo):
    native_version, revision = bind920_profile.recipe_version(makefile.decode(), "bind920")
    version = native_version + ("_" + str(revision) if revision else "")
    source = creator_target["recipe"]
    profile = bind920_profile.profile_from_recipe(dict(version=version, ports_commit=source["commit"]),
                                                  source["commit"], makefile, distinfo, "bind920")
    return dict(name="bind920", version=bind920_profile.package_version(profile), origin="dns/bind920",
                abi=creator_target["package_creator"]["abi"], release=upstream["tools_tag"],
                source=dict(repository=target_pkg.PORTS_REPOSITORY, commit=source["commit"], path="dns/bind920"),
                makefile_sha256=hashlib.sha256(makefile).hexdigest(), distinfo_sha256=hashlib.sha256(distinfo).hexdigest())


def validate(record, upstream, creator_target):
    if (not isinstance(record, dict) or set(record) != {"name", "version", "origin", "abi", "release", "source",
                                                      "makefile_sha256", "distinfo_sha256"}
            or record["name"] != "bind920" or record["origin"] != "dns/bind920"
            or record["abi"] != creator_target["package_creator"]["abi"]
            or record["release"] != upstream["tools_tag"]
            or record["source"] != dict(repository=target_pkg.PORTS_REPOSITORY,
                                       commit=creator_target["recipe"]["commit"], path="dns/bind920")):
        raise ValueError("ordinary BIND dependency differs from frozen upstream recipe")
    native_version = bind920_profile.package_version(dict(ports_repository=bind920_profile.PORTS_REPOSITORY,
        ports_commit=record["source"]["commit"], makefile_sha256=record["makefile_sha256"],
        distinfo_sha256=record["distinfo_sha256"], distversion=record["version"].split("_")[0],
        portrevision=int(record["version"].split("_")[1]) if "_" in record["version"] else 0))
    if native_version != record["version"]:
        raise ValueError("ordinary dependency version is not canonical")
    return record


def resolve(upstream, creator_target):
    commit = creator_target["recipe"]["commit"]
    blobs = []
    for name in ("Makefile", "distinfo"):
        payload = release_channel.gh_api(f"repos/opnsense/ports/contents/dns/bind920/{name}?ref={commit}")
        if payload.get("encoding") != "base64":
            raise ValueError("ordinary BIND recipe is not an immutable Git blob")
        blobs.append(base64.b64decode(payload["content"], validate=False))
    return validate(from_recipe(upstream, creator_target, *blobs), upstream, creator_target)
