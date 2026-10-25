#!/usr/bin/env python3
"""Generate Vision's three-target Xcode project using only Python's standard library.

Generated files contain public configuration and signing identifiers, never credentials.
The Swift sources remain editable in App/, Shared/, ShareExtension/, and ActivityWidget/.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zlib


IOS = Path(__file__).resolve().parent
REPO = IOS.parent
DEFAULT_SERVER = "https://desktop-vjt2br2.tail385c9d.ts.net"
DEFAULT_WEB = "https://jrdn-r.github.io/vision/"
TARGETS = ("Vision", "VortexShare", "VortexActivity")
TEST_TARGET = "VisionTests"


def identifier(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()[:24].upper()


def openstep(value, depth: int = 0) -> str:
    pad = "\t" * depth
    if isinstance(value, dict):
        lines = ["{"]
        for key, item in value.items():
            lines.append(f"{pad}\t{json.dumps(str(key))} = {openstep(item, depth + 1)};")
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        return "(\n" + "".join(f"{pad}\t{openstep(item, depth + 1)},\n" for item in value) + pad + ")"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def write_plist(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(value, sort_keys=False))


def https_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(ord(c) < 33 for c in value)):
        raise argparse.ArgumentTypeError("Use a public HTTPS URL without credentials, query, or fragment.")
    try:
        parsed.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("The HTTPS URL has an invalid port.") from error
    return value


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def portable_icon(source: Path, destination: Path) -> None:
    """Scale the repository's RGB/RGBA PNG without any Pillow dependency on CI/Linux."""
    blob = source.read_bytes()
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Vision icon is not a PNG")
    cursor, compressed, header = 8, bytearray(), None
    while cursor < len(blob):
        size = struct.unpack(">I", blob[cursor:cursor + 4])[0]
        kind = blob[cursor + 4:cursor + 8]
        data = blob[cursor + 8:cursor + 8 + size]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            compressed.extend(data)
        cursor += 12 + size
    if not header:
        raise ValueError("Vision icon has no PNG header")
    width, height, bits, color, compression, filtering, interlace = header
    if bits != 8 or color not in (2, 6) or interlace or compression or filtering:
        raise ValueError("The portable icon converter requires a noninterlaced RGB/RGBA 8-bit PNG")
    channels = 3 if color == 2 else 4
    stride = width * channels
    raw = zlib.decompress(compressed)
    rows, previous, offset = [], bytearray(stride), 0

    def paeth(a, b, c):
        p = a + b - c
        values = (abs(p - a), abs(p - b), abs(p - c))
        return (a, b, c)[values.index(min(values))]

    for _ in range(height):
        mode = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + stride])
        offset += stride
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if mode == 1:
                row[i] = (row[i] + left) & 255
            elif mode == 2:
                row[i] = (row[i] + up) & 255
            elif mode == 3:
                row[i] = (row[i] + (left + up) // 2) & 255
            elif mode == 4:
                row[i] = (row[i] + paeth(left, up, corner)) & 255
            elif mode != 0:
                raise ValueError("Unsupported PNG filter")
        rows.append(row)
        previous = row
    # An opaque RGB App Store icon also works for development installation.
    output = bytearray()
    for y in range(1024):
        row = rows[y * height // 1024]
        output.append(0)
        for x in range(1024):
            pixel = row[(x * width // 1024) * channels:][:channels]
            alpha = pixel[3] if channels == 4 else 255
            output.extend((value * alpha // 255 for value in pixel[:3]))
    destination.write_bytes(b"\x89PNG\r\n\x1a\n" +
                            png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 1024, 8, 2, 0, 0, 0)) +
                            png_chunk(b"IDAT", zlib.compress(output, 9)) + png_chunk(b"IEND", b""))


def make_assets() -> None:
    assets = IOS / "Generated/Assets.xcassets"
    icons = assets / "AppIcon.appiconset"
    icons.mkdir(parents=True, exist_ok=True)
    (assets / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n")
    source = REPO / "Vision-icon-512.png"
    destination = icons / "Vision-1024.png"
    if not source.is_file():
        raise ValueError("The Vision-icon-512.png repository icon is missing")
    # sips produces a smooth enlargement on the user's Mac; the portable path
    # permits full generator verification in Linux without added packages.
    if sys.platform == "darwin" and Path("/usr/bin/sips").is_file():
        subprocess.run(["/usr/bin/sips", "-z", "1024", "1024", str(source), "--out", str(destination)],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        portable_icon(source, destination)
    (icons / "Contents.json").write_text(json.dumps({
        "images": [{"filename": "Vision-1024.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}],
        "info": {"author": "xcode", "version": 1},
    }, indent=2) + "\n")
    write_plist(IOS / "Generated/PrivacyInfo.xcprivacy", {
        "NSPrivacyTracking": False,
        "NSPrivacyAccessedAPITypes": [
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults", "NSPrivacyAccessedAPITypeReasons": ["1C8F.1"]},
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryFileTimestamp", "NSPrivacyAccessedAPITypeReasons": ["C617.1"]},
        ],
    })


def create_plists(args, bundle: str, group: str, keychain: str) -> dict[str, str]:
    bundle_ids = {"Vision": bundle, "VortexShare": bundle + ".share", "VortexActivity": bundle + ".activity", "VisionTests": bundle + ".tests"}
    for name in TARGETS:
        info = {
            "CFBundleDevelopmentRegion": "$(DEVELOPMENT_LANGUAGE)",
            "CFBundleDisplayName": "Vision" if name == "Vision" else "Vortex",
            "CFBundleExecutable": "$(EXECUTABLE_NAME)",
            "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)",
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": "$(PRODUCT_NAME)",
            "CFBundlePackageType": "APPL" if name == "Vision" else "XPC!",
            "CFBundleShortVersionString": "$(MARKETING_VERSION)",
            "CFBundleVersion": "$(CURRENT_PROJECT_VERSION)",
            "VisionServerURL": args.server_url.rstrip("/"),
            "VisionWebURL": args.web_url.rstrip("/") + "/",
            "VisionAppGroup": group,
            "VisionKeychainGroup": "$(AppIdentifierPrefix)" + keychain,
            "VisionPushEnabled": args.push,
        }
        entitlements = {"com.apple.security.application-groups": [group],
                        "keychain-access-groups": ["$(AppIdentifierPrefix)" + keychain]}
        if name == "Vision":
            info.update({
                "LSRequiresIPhoneOS": True,
                "CFBundleURLTypes": [{"CFBundleURLName": bundle + ".auth", "CFBundleURLSchemes": ["visioncompanion"]},
                                     {"CFBundleURLName": bundle + ".navigation", "CFBundleURLSchemes": ["visionapp"]}],
                "NSSupportsLiveActivities": True,
                "NSSupportsLiveActivitiesFrequentUpdates": args.push,
                "NSPhotoLibraryAddUsageDescription": "Save videos downloaded with Vortex to your Photos library.",
                "NSCameraUsageDescription": "Use the camera when you choose to attach media in Vision.",
                "NSMicrophoneUsageDescription": "Use the microphone when you choose to record media in Vision.",
                "UIApplicationSceneManifest": {"UIApplicationSupportsMultipleScenes": False},
                "UILaunchScreen": {},
                "UIFileSharingEnabled": False,
                "LSSupportsOpeningDocumentsInPlace": True,
                "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait"],
                "UISupportedInterfaceOrientations~ipad": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationPortraitUpsideDown",
                                                          "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
            })
            if args.push:
                entitlements["aps-environment"] = "development"
                info["UIBackgroundModes"] = ["remote-notification"]
        elif name == "VortexShare":
            info["NSPhotoLibraryAddUsageDescription"] = "Save videos downloaded with Vortex to your Photos library."
            info["NSExtension"] = {
                "NSExtensionPointIdentifier": "com.apple.share-services",
                "NSExtensionPrincipalClass": "$(PRODUCT_MODULE_NAME).ShareViewController",
                "NSExtensionAttributes": {"NSExtensionActivationRule": {
                    "NSExtensionActivationSupportsText": True,
                    "NSExtensionActivationSupportsWebURLWithMaxCount": 10,
                    "NSExtensionActivationSupportsWebPageWithMaxCount": 10,
                }},
            }
        else:
            info["NSExtension"] = {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"}
        write_plist(IOS / f"Generated/{name}-Info.plist", info)
        write_plist(IOS / f"Generated/{name}.entitlements", entitlements)
    return bundle_ids


def create_project(args, bundle_ids: dict[str, str]) -> tuple[dict, dict]:
    objects = {}

    def add(key: str, isa: str, **values) -> str:
        object_id = identifier(key)
        if object_id in objects:
            raise ValueError("Duplicate project object: " + key)
        objects[object_id] = {"isa": isa, **values}
        return object_id

    project_id = identifier("Project")
    target_ids = {name: identifier("Target:" + name) for name in (*TARGETS, TEST_TARGET)}
    source_sets = {
        "Vision": sorted((IOS / "App").rglob("*.swift")) + sorted((IOS / "Shared").rglob("*.swift")),
        "VortexShare": sorted((IOS / "ShareExtension").rglob("*.swift")) +
                       [path for path in sorted((IOS / "Shared").rglob("*.swift")) if path.name != "VortexActivityAttributes.swift"],
        "VortexActivity": sorted((IOS / "ActivityWidget").rglob("*.swift")) + [IOS / "Shared/VortexActivityAttributes.swift"],
        "VisionTests": sorted((IOS / "Tests").rglob("*.swift")),
    }
    for name, directory in [("Vision", "App"), ("VortexShare", "ShareExtension"), ("VortexActivity", "ActivityWidget")]:
        if not any(path.parent.name == directory for path in source_sets[name]):
            raise ValueError(f"No Swift source files found in ios/{directory}/; download the complete repository first")
    refs = {}
    group_children = {folder: [] for folder in ["App", "Shared", "ShareExtension", "ActivityWidget", "Tests"]}
    for path in sorted(set(sum(source_sets.values(), []))):
        if not path.is_file():
            raise ValueError(f"Required source is missing: {path}")
        relative = path.relative_to(IOS).as_posix()
        refs[relative] = add("File:" + relative, "PBXFileReference", lastKnownFileType="sourcecode.swift", path=relative, sourceTree="<group>")
        group_children[relative.split("/")[0]].append(refs[relative])
    source_groups = [add("Group:" + folder, "PBXGroup", name=folder, children=children, sourceTree="<group>")
                     for folder, children in group_children.items()]
    framework_names = ["Foundation", "UIKit", "SwiftUI", "Combine", "WebKit", "AuthenticationServices", "Security",
                       "CryptoKit", "Photos", "UniformTypeIdentifiers", "ActivityKit", "WidgetKit", "UserNotifications"]
    frameworks = {name: add("Framework:" + name, "PBXFileReference", lastKnownFileType="wrapper.framework",
                           name=name + ".framework", path=f"System/Library/Frameworks/{name}.framework", sourceTree="SDKROOT")
                  for name in framework_names}
    framework_group = add("Group:Frameworks", "PBXGroup", name="Frameworks", children=list(frameworks.values()), sourceTree="<group>")
    asset_ref = add("File:Assets", "PBXFileReference", lastKnownFileType="folder.assetcatalog", path="Generated/Assets.xcassets", sourceTree="<group>")
    privacy_ref = add("File:Privacy", "PBXFileReference", lastKnownFileType="text.xml", path="Generated/PrivacyInfo.xcprivacy", sourceTree="<group>")
    products = {name: add("Product:" + name, "PBXFileReference", explicitFileType="wrapper.application" if name == "Vision" else ("wrapper.cfbundle" if name == TEST_TARGET else "wrapper.app-extension"),
                         includeInIndex=0, path=name + (".app" if name == "Vision" else (".xctest" if name == TEST_TARGET else ".appex")), sourceTree="BUILT_PRODUCTS_DIR")
                for name in (*TARGETS, TEST_TARGET)}
    product_group = add("Group:Products", "PBXGroup", name="Products", children=list(products.values()), sourceTree="<group>")
    main_group = add("Group:Root", "PBXGroup", children=source_groups + [asset_ref, privacy_ref, framework_group, product_group], sourceTree="<group>")

    project_configs = []
    for config in ["Debug", "Release"]:
        project_configs.append(add("Config:Project:" + config, "XCBuildConfiguration", name=config, buildSettings={
            "CLANG_ENABLE_MODULES": "YES", "CLANG_ENABLE_OBJC_ARC": "YES", "SDKROOT": "iphoneos",
            "IPHONEOS_DEPLOYMENT_TARGET": "17.0", "SWIFT_VERSION": "5.0", "SWIFT_STRICT_CONCURRENCY": "targeted",
            "SWIFT_OPTIMIZATION_LEVEL": "-Onone" if config == "Debug" else "-O",
            "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG $(inherited)" if config == "Debug" else "$(inherited)",
            "DEBUG_INFORMATION_FORMAT": "dwarf" if config == "Debug" else "dwarf-with-dsym",
            "ENABLE_TESTABILITY": "YES" if config == "Debug" else "NO", "ONLY_ACTIVE_ARCH": "YES" if config == "Debug" else "NO",
        }))
    project_config_list = add("ConfigList:Project", "XCConfigurationList", buildConfigurations=project_configs,
                              defaultConfigurationIsVisible=0, defaultConfigurationName="Release")

    for name in (*TARGETS, TEST_TARGET):
        source_build_files = [add(f"Build:{name}:{path.relative_to(IOS)}", "PBXBuildFile", fileRef=refs[path.relative_to(IOS).as_posix()])
                              for path in source_sets[name]]
        source_phase = add("Sources:" + name, "PBXSourcesBuildPhase", buildActionMask=2147483647, files=source_build_files, runOnlyForDeploymentPostprocessing=0)
        used_frameworks = [] if name == TEST_TARGET else framework_names if name == "Vision" else (
            ["Foundation", "UIKit", "Security", "Photos", "UniformTypeIdentifiers"] if name == "VortexShare" else ["Foundation", "SwiftUI", "WidgetKit", "ActivityKit"])
        framework_build_files = [add(f"Link:{name}:{framework}", "PBXBuildFile", fileRef=frameworks[framework]) for framework in used_frameworks]
        framework_phase = add("Frameworks:" + name, "PBXFrameworksBuildPhase", buildActionMask=2147483647, files=framework_build_files, runOnlyForDeploymentPostprocessing=0)
        resources = [add("Build:Vision:Assets", "PBXBuildFile", fileRef=asset_ref)] if name == "Vision" else []
        if name in ("Vision", "VortexShare"):
            resources.append(add("Build:" + name + ":Privacy", "PBXBuildFile", fileRef=privacy_ref))
        resources_phase = add("Resources:" + name, "PBXResourcesBuildPhase", buildActionMask=2147483647, files=resources, runOnlyForDeploymentPostprocessing=0)
        phases, dependencies = [source_phase, framework_phase, resources_phase], []
        if name == "Vision":
            embeds = []
            for extension in TARGETS[1:]:
                embeds.append(add("Embed:" + extension, "PBXBuildFile", fileRef=products[extension], settings={"ATTRIBUTES": ["RemoveHeadersOnCopy"]}))
                proxy = add("Proxy:" + extension, "PBXContainerItemProxy", containerPortal=project_id, proxyType=1,
                            remoteGlobalIDString=target_ids[extension], remoteInfo=extension)
                dependencies.append(add("Dependency:" + extension, "PBXTargetDependency", target=target_ids[extension], targetProxy=proxy))
            phases.append(add("EmbedExtensions", "PBXCopyFilesBuildPhase", buildActionMask=2147483647, dstPath="", dstSubfolderSpec=13,
                              name="Embed App Extensions", files=embeds, runOnlyForDeploymentPostprocessing=0))
        elif name == TEST_TARGET:
            proxy = add("Proxy:TestHost", "PBXContainerItemProxy", containerPortal=project_id, proxyType=1,
                        remoteGlobalIDString=target_ids["Vision"], remoteInfo="Vision")
            dependencies.append(add("Dependency:TestHost", "PBXTargetDependency", target=target_ids["Vision"], targetProxy=proxy))
        configs = []
        for config in ["Debug", "Release"]:
            settings = {
                "PRODUCT_NAME": "$(TARGET_NAME)", "PRODUCT_BUNDLE_IDENTIFIER": bundle_ids[name],
                "INFOPLIST_FILE": f"Generated/{name}-Info.plist", "GENERATE_INFOPLIST_FILE": "NO",
                "CODE_SIGN_STYLE": "Automatic", "CODE_SIGN_ENTITLEMENTS": f"Generated/{name}.entitlements",
                "CURRENT_PROJECT_VERSION": "2", "MARKETING_VERSION": "1.1.0", "TARGETED_DEVICE_FAMILY": "1,2",
                "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator", "SUPPORTS_MACCATALYST": "NO",
                "SUPPORTS_MAC_DESIGNED_FOR_IPHONE_IPAD": "NO", "SWIFT_EMIT_LOC_STRINGS": "YES",
                "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks"] + ([] if name == "Vision" else ["@executable_path/../../Frameworks"]),
            }
            if args.team:
                settings["DEVELOPMENT_TEAM"] = args.team
            if name == "Vision":
                settings["ASSETCATALOG_COMPILER_APPICON_NAME"] = "AppIcon"
            elif name == TEST_TARGET:
                settings.pop("INFOPLIST_FILE")
                settings.pop("CODE_SIGN_ENTITLEMENTS")
                settings.update({"GENERATE_INFOPLIST_FILE": "YES", "BUNDLE_LOADER": "$(TEST_HOST)",
                                 "TEST_HOST": "$(BUILT_PRODUCTS_DIR)/Vision.app/Vision", "SKIP_INSTALL": "YES"})
            else:
                settings["APPLICATION_EXTENSION_API_ONLY"] = "YES"
                settings["SKIP_INSTALL"] = "YES"
            configs.append(add(f"Config:{name}:{config}", "XCBuildConfiguration", name=config, buildSettings=settings))
        config_list = add("ConfigList:" + name, "XCConfigurationList", buildConfigurations=configs, defaultConfigurationIsVisible=0, defaultConfigurationName="Release")
        add("Target:" + name, "PBXNativeTarget", buildConfigurationList=config_list, buildPhases=phases, buildRules=[], dependencies=dependencies,
            name=name, productName=name, productReference=products[name], productType="com.apple.product-type.application" if name == "Vision" else ("com.apple.product-type.bundle.unit-test" if name == TEST_TARGET else "com.apple.product-type.app-extension"))
    attrs = {target_id: {"CreatedOnToolsVersion": "16.0", "ProvisioningStyle": "Automatic", **({"DevelopmentTeam": args.team} if args.team else {})}
             for target_id in target_ids.values()}
    add("Project", "PBXProject", attributes={"BuildIndependentTargetsInParallel": "YES", "LastUpgradeCheck": "1600", "TargetAttributes": attrs},
        buildConfigurationList=project_config_list, compatibilityVersion="Xcode 14.0", developmentRegion="en", hasScannedForEncodings=0,
        knownRegions=["en", "Base"], mainGroup=main_group, productRefGroup=product_group, projectDirPath="", projectRoot="", targets=list(target_ids.values()))
    model = {"archiveVersion": 1, "classes": {}, "objectVersion": 56, "objects": objects, "rootObject": project_id}
    project = IOS / "Vision.xcodeproj"
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.pbxproj").write_text("// !$*UTF8*$!\n" + openstep(model) + "\n")
    workspace = project / "project.xcworkspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "contents.xcworkspacedata").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<Workspace version="1.0"><FileRef location="self:"/></Workspace>\n')
    scheme = ET.Element("Scheme", LastUpgradeVersion="1600", version="1.3")
    action = ET.SubElement(scheme, "BuildAction", parallelizeBuildables="YES", buildImplicitDependencies="YES")
    entries = ET.SubElement(action, "BuildActionEntries")
    entry = ET.SubElement(entries, "BuildActionEntry", buildForTesting="YES", buildForRunning="YES", buildForProfiling="YES", buildForArchiving="YES", buildForAnalyzing="YES")

    def build_reference(parent, name="Vision"):
        ET.SubElement(parent, "BuildableReference", BuildableIdentifier="primary", BlueprintIdentifier=target_ids[name],
                      BuildableName=name + (".xctest" if name == TEST_TARGET else ".app"), BlueprintName=name, ReferencedContainer="container:Vision.xcodeproj")

    build_reference(entry)
    test_entry = ET.SubElement(entries, "BuildActionEntry", buildForTesting="YES", buildForRunning="NO", buildForProfiling="NO", buildForArchiving="NO", buildForAnalyzing="YES")
    build_reference(test_entry, TEST_TARGET)
    test_action = ET.SubElement(scheme, "TestAction", buildConfiguration="Debug", selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB",
                               selectedLauncherIdentifier="Xcode.DebuggerFoundation.Launcher.LLDB", shouldUseLaunchSchemeArgsEnv="YES")
    testables = ET.SubElement(test_action, "Testables")
    testable = ET.SubElement(testables, "TestableReference", skipped="NO")
    build_reference(testable, TEST_TARGET)
    launch = ET.SubElement(scheme, "LaunchAction", buildConfiguration="Debug", selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB",
                           selectedLauncherIdentifier="Xcode.DebuggerFoundation.Launcher.LLDB", launchStyle="0", useCustomWorkingDirectory="NO", ignoresPersistentStateOnLaunch="NO",
                           debugDocumentVersioning="YES", debugServiceExtension="internal", allowLocationSimulation="YES")
    build_reference(ET.SubElement(launch, "BuildableProductRunnable", runnableDebuggingMode="0"))
    profile = ET.SubElement(scheme, "ProfileAction", buildConfiguration="Release", shouldUseLaunchSchemeArgsEnv="YES", savedToolIdentifier="",
                            useCustomWorkingDirectory="NO", debugDocumentVersioning="YES")
    build_reference(ET.SubElement(profile, "BuildableProductRunnable", runnableDebuggingMode="0"))
    ET.SubElement(scheme, "AnalyzeAction", buildConfiguration="Debug")
    ET.SubElement(scheme, "ArchiveAction", buildConfiguration="Release", revealArchiveInOrganizer="YES")
    schemes = project / "xcshareddata/xcschemes"
    schemes.mkdir(parents=True, exist_ok=True)
    ET.indent(scheme, space="  ")
    ET.ElementTree(scheme).write(schemes / "Vision.xcscheme", encoding="utf-8", xml_declaration=True)
    return model, source_sets


def validate(model: dict, bundle_ids: dict, group: str, keychain: str, push: bool) -> None:
    objects = model["objects"]
    native_targets = [item for item in objects.values() if item["isa"] == "PBXNativeTarget"]
    assert {item["name"] for item in native_targets} == set((*TARGETS, TEST_TARGET))
    app = next(item for item in native_targets if item["name"] == "Vision")
    assert len(app["dependencies"]) == 2
    embed = objects[identifier("EmbedExtensions")]
    assert embed["dstSubfolderSpec"] == 13 and len(embed["files"]) == 2
    assert len(set(bundle_ids.values())) == 4
    for target in TARGETS:
        info = plistlib.loads((IOS / f"Generated/{target}-Info.plist").read_bytes())
        entitlements = plistlib.loads((IOS / f"Generated/{target}.entitlements").read_bytes())
        assert entitlements["com.apple.security.application-groups"] == [group]
        assert entitlements["keychain-access-groups"] == ["$(AppIdentifierPrefix)" + keychain]
        assert info["VisionAppGroup"] == group
        assert info["VisionKeychainGroup"] == "$(AppIdentifierPrefix)" + keychain
        assert info["VisionPushEnabled"] == push
        assert ("aps-environment" in entitlements) == (target == "Vision" and push)
        if target != "Vision":
            assert bundle_ids[target].startswith(bundle_ids["Vision"] + ".")
        for configuration in ["Debug", "Release"]:
            settings = objects[identifier(f"Config:{target}:{configuration}")]["buildSettings"]
            assert settings["PRODUCT_BUNDLE_IDENTIFIER"] == bundle_ids[target]
            assert (IOS / settings["INFOPLIST_FILE"]).is_file()
            assert (IOS / settings["CODE_SIGN_ENTITLEMENTS"]).is_file()
    for obj in objects.values():
        for key in ["fileRef", "target", "targetProxy", "containerPortal", "remoteGlobalIDString", "productReference", "buildConfigurationList", "mainGroup", "productRefGroup"]:
            if key in obj:
                assert obj[key] in objects, (key, obj[key])
        for key in ["children", "files", "buildPhases", "dependencies", "buildConfigurations", "targets"]:
            for value in obj.get(key, []):
                assert value in objects, (key, value)
    icon = (IOS / "Generated/Assets.xcassets/AppIcon.appiconset/Vision-1024.png").read_bytes()
    assert struct.unpack(">II", icon[16:24]) == (1024, 1024)
    ET.parse(IOS / "Vision.xcodeproj/xcshareddata/xcschemes/Vision.xcscheme")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", default="", help="10-character Apple development Team ID")
    parser.add_argument("--bundle-id", default="", help="Stable bundle ID; defaults to com.jordanrapp.Vision.<team>")
    parser.add_argument("--server-url", default=DEFAULT_SERVER, type=https_url)
    parser.add_argument("--web-url", default=DEFAULT_WEB, type=https_url)
    parser.add_argument("--push", action="store_true", help="Enable APNs entitlements; requires paid Apple Developer Program membership")
    parser.add_argument("--self-check", action="store_true", help="Print detailed generated-project validation results")
    args = parser.parse_args()
    if args.team and not re.fullmatch(r"[A-Z0-9]{10}", args.team):
        parser.error("--team must be the 10-character Apple development Team ID")
    bundle = args.bundle_id or ("com.jordanrapp.Vision." + args.team.lower() if args.team else "com.jordanrapp.Vision")
    if len(bundle) > 180 or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+){2,}", bundle):
        parser.error("--bundle-id must be a valid reverse-domain identifier without spaces or wildcards")
    group, keychain = "group." + bundle, bundle + ".shared"
    bundle_ids = create_plists(args, bundle, group, keychain)
    make_assets()
    model, sources = create_project(args, bundle_ids)
    validate(model, bundle_ids, group, keychain, args.push)
    metadata = {"bundleID": bundle, "appGroup": group, "keychainGroup": keychain, "teamID": args.team, "pushEnabled": args.push,
                "serverURL": args.server_url.rstrip("/"), "webURL": args.web_url.rstrip("/") + "/", "targets": bundle_ids}
    (IOS / "Generated/project-settings.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Generated {IOS / 'Vision.xcodeproj'}")
    print(f"App: {bundle}; App Group: {group}; push: {'enabled (paid membership)' if args.push else 'off (Personal Team compatible)'}")
    if args.self_check:
        print("Verified: app + 2 embedded extensions + XCTest target, unique bundle IDs, shared groups, complete references, signing profiles, plist/scheme syntax, 1024px icon.")
        print("Swift sources: " + ", ".join(f"{name}={len(files)}" for name, files in sources.items()))
        print("Swift compilation still requires xcodebuild on macOS.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Project generation failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
