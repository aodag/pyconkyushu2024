{
  description = "my project description";

  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem
      (system:
        let
          pkgs = import nixpkgs {
            inherit system;
          };
        in
        {
          devShells.default = with pkgs;
            mkShell {
              buildInputs = [
                texliveFull
                (python3.withPackages (python-packages: with python-packages; [
                  pip
                ]))
                pyright
                ruff
              ];
            };
        }
      );
}
