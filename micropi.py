from typing import Iterable, TypedDict, NotRequired
import logging
import itertools
import re
import argparse
import json
import pathlib
from wsgiref import simple_server
from wsgiref.types import WSGIApplication, WSGIEnvironment, StartResponse
import zipfile

logger = logging.getLogger(__name__)


Wheel = TypedDict(
    "Wheel",
    {
        "data": pathlib.Path,
        "metadata": bytes,
    },
)


Meta = TypedDict(
    "Meta",
    {
        "api-version": str,
    },
)

ProjectFile = TypedDict(
    "ProjectFile",
    {
        "filename": str,
        "url": str,
        "hashes": dict[str, str],
        "requires-python": NotRequired[str],
        "dist-info-metadata": NotRequired[str],
        "gpg-sig": NotRequired[bool],
        "yanked": NotRequired[bool],
    },
)

ProjectDetail = TypedDict(
    "ProjectDetail",
    {
        "name": str,
        "files": list[ProjectFile],
        "meta": Meta,
    },
)

Project = TypedDict("Project", {"name": str})
ProjectList = TypedDict(
    "ProjectList",
    {
        "meta": Meta,
        "projects": list[Project],
    },
)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_metadata(whl: pathlib.Path) -> bytes:
    parts = whl.name.split("-")
    dist_name, version = parts[0], parts[1]
    metadata_path = f"{dist_name}-{version}.dist-info/METADATA"
    with zipfile.ZipFile(whl) as zf:
        with zf.open(metadata_path) as metadata:
            return metadata.read()


def standard_layout(
    body: Iterable[bytes], pypi_repository_version="1.0", encoding="utf8"
) -> Iterable[bytes]:
    return itertools.chain(
        [
            b"<html>",
            b"<head>",
            f'<meta name="pypi:repository-version" content="{pypi_repository_version}">'.encode(
                encoding
            ),
            b"<body>",
        ],
        body,
        [b"</body>", b"</html>"],
    )


class PackageRepository:
    pypi_repository_version = "1.0"

    def __init__(self, wheels: dict[str, Wheel]) -> None:
        self.wheels = wheels

    def get_metadata(self, wheel_name) -> bytes:
        return self.wheels[wheel_name]["metadata"]

    def get_projects(self) -> Iterable[Project]:
        return [
            {"name": normalize(w)}
            for w in set([whl.split("-", 1)[0] for whl in self.wheels])
        ]

    def get_data(self, whl) -> bytes:
        with self.wheels[whl]["data"].open("br") as f:
            return f.read()

    def load_project(self, project_name: str) -> ProjectDetail:
        return {
            "name": normalize(project_name),
            "meta": {
                "api-version": self.pypi_repository_version,
            },
            "files": self.load_project_files(project_name),
        }

    def load_project_files(self, project_name: str) -> list[ProjectFile]:
        return [
            {
                "filename": wheel_name,
                "url": f"/{project_name}/files/{wheel_name}",
                "hashes": {},
            }
            for wheel_name in self.wheels
            if normalize(wheel_name).startswith(normalize(project_name))
        ]


class AcceptDispatch:
    def __init__(self, jsonapp: WSGIApplication, htmlapp: WSGIApplication) -> None:
        self.jsonapp = jsonapp
        self.htmlapp = htmlapp

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        if self.acceptable_json(environ):
            return self.jsonapp(environ, start_response)
        else:
            return self.htmlapp(environ, start_response)

    def acceptable_json(self, environ: WSGIEnvironment) -> bool:
        accepts = [a.strip() for a in environ["HTTP_ACCEPT"].split(",")]
        logger.debug("ACCEPT: %s", accepts)
        return any([a.startswith("application/json") for a in accepts])


class RegexDispatch:
    patterns: list[tuple[re.Pattern, WSGIApplication]]

    def __init__(self, patterns):
        self.patterns = patterns

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        script_name = environ.get("SCRIPT_NAME", "")
        path_info = environ.get("PATH_INFO", "")
        for regex, application in self.patterns:
            m = regex.match(path_info)
            if not m:
                continue
            extra_path_info = path_info[m.end() :]
            if extra_path_info and not extra_path_info.startswith("/"):
                # Not a very good match
                continue
            pos_args = m.groups()
            named_args = m.groupdict()
            cur_pos, cur_named = environ.get("wsgiorg.routing_args", ((), {}))
            new_pos = list(cur_pos) + list(pos_args)
            new_named = cur_named.copy()
            new_named.update(named_args)
            environ["wsgiorg.routing_args"] = (new_pos, new_named)
            environ["SCRIPT_NAME"] = script_name + path_info[: m.end()]
            environ["PATH_INFO"] = extra_path_info
            return application(environ, start_response)
        return self.not_found(environ, start_response)

    def not_found(
        self, _: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        start_response("404 Not Found", [("Content-type", "text/plain")])
        return [b"Not found"]


class JsonProjectsApp:
    def __init__(self, repo: PackageRepository):
        self.repo = repo

    def __call__(
        self, _: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        start_response(
            "200 OK", [("content-type", "application/vnd.pypi.simple.v1+json")]
        )
        projects = list(self.repo.get_projects())
        project_list: ProjectList = {
            "meta": {"api-version": "1.0"},
            "projects": projects,
        }
        return [json.dumps(project_list).encode("utf8")]


class JsonProjectDetailApp:
    def __init__(self, repo: PackageRepository) -> None:
        self.repo = repo

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        project_name = environ["wsgiorg.routing_args"][1]["project_name"]
        start_response(
            "200 OK", [("content-type", "application/vnd.pypi.simple.v1+json")]
        )
        project_detail = self.repo.load_project(project_name)
        return [json.dumps(project_detail).encode("utf8")]


class ProjectsApp:
    def __init__(self, repo: PackageRepository) -> None:
        self.repo = repo

    def __call__(
        self, _: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        start_response("200 OK", [("content-type", "text/html")])
        return standard_layout(
            [b"<ul>"]
            + [
                f'<li><a href="/files/{w}" data-dist-info-metadata="{w}.metadata">{w}</a></li>'.encode(
                    "utf8"
                )
                for w in self.repo.wheels
            ]
            + [b"</ul>"]
        )


class MetadataApp:
    def __init__(self, repo: PackageRepository) -> None:
        self.repo = repo

    def __call__(self, environ, start_response) -> Iterable[bytes]:
        whl = environ["wsgiorg.routing_args"][1]["wheel_name"]
        start_response("200 OK", [("Content-type", "application/octet-stream")])
        return [self.repo.get_metadata(whl)]


class WheelApp:
    def __init__(self, repo: PackageRepository) -> None:
        self.repo = repo

    def __call__(self, environ, start_response) -> Iterable[bytes]:
        whl = environ["wsgiorg.routing_args"][1]["wheel_name"]
        start_response("200 OK", [("Content-type", "application/octet-stream")])
        return [self.repo.get_data(whl)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5000, type=int)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("wheels", type=pathlib.Path)

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    wheels: dict[str, Wheel] = {
        w.name: {"data": w, "metadata": load_metadata(w)}
        for w in args.wheels.glob("*.whl")
    }
    repo = PackageRepository(wheels)
    htmlapp = RegexDispatch(
        [
            (re.compile("^/$"), ProjectsApp(repo)),
            (re.compile("^/files/(?P<wheel_name>.*.whl)$"), WheelApp(repo)),
            (re.compile("^/files/(?P<wheel_name>.*.whl).metadata$"), MetadataApp(repo)),
        ]
    )
    jsonapp = RegexDispatch(
        [
            (re.compile("^/$"), JsonProjectsApp(repo)),
            (re.compile("^/(?P<project_name>.*)/$"), JsonProjectDetailApp(repo)),
            (
                re.compile("^/(?P<project_name>.*)/files/(?P<wheel_name>.*.whl)$"),
                WheelApp(repo),
            ),
        ]
    )

    httpd = simple_server.make_server(args.host, args.port, jsonapp)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
