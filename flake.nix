{
  description = "GenericAgent development shell and terminal UI package";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = lib.genAttrs systems;
      source = lib.cleanSourceWith {
        src = ./.;
        filter = path: type:
          (lib.cleanSourceFilter path type)
          && !(builtins.elem (builtins.baseNameOf path) [
            ".omx"
            "result"
          ]);
      };
      pythonEnvFor = pkgs:
        pkgs.python312.withPackages (ps: with ps; [
          aiohttp
          beautifulsoup4
          bottle
          pillow
          prompt-toolkit
          psutil
          requests
          rich
          simple-websocket-server
          textual
          tkinter
          urllib3
        ]);
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          pythonEnv = pythonEnvFor pkgs;
          runtimePath = lib.makeBinPath [
            pythonEnv
            pkgs.git
          ];
        in
        rec {
          genericagent-tui = pkgs.stdenvNoCC.mkDerivation {
            pname = "genericagent-tui";
            version = "0.1.0";
            src = source;

            nativeBuildInputs = [ pkgs.makeWrapper ];

            dontConfigure = true;
            dontBuild = true;

            installPhase = ''
              runHook preInstall

              mkdir -p $out/share/genericagent $out/bin
              cp -R . $out/share/genericagent
              chmod -R u+w $out/share/genericagent

              makeWrapper ${pythonEnv}/bin/python $out/bin/ga \
                --run 'runtime_dir="''${GENERICAGENT_HOME:-''${XDG_DATA_HOME:-$HOME/.local/share}/genericagent}"' \
                --run 'if [ ! -e "$runtime_dir/ga_cli" ]; then mkdir -p "$runtime_dir"; cp -R "'$out'/share/genericagent/." "$runtime_dir/"; chmod -R u+w "$runtime_dir"; fi' \
                --run 'cd "$runtime_dir"' \
                --run 'export PYTHONPATH="$runtime_dir''${PYTHONPATH:+:$PYTHONPATH}"' \
                --prefix PATH : ${runtimePath} \
                --add-flags -m \
                --add-flags ga_cli

              makeWrapper ${pythonEnv}/bin/python $out/bin/genericagent-tui \
                --run 'runtime_dir="''${GENERICAGENT_HOME:-''${XDG_DATA_HOME:-$HOME/.local/share}/genericagent}"' \
                --run 'if [ ! -e "$runtime_dir/ga_cli" ]; then mkdir -p "$runtime_dir"; cp -R "'$out'/share/genericagent/." "$runtime_dir/"; chmod -R u+w "$runtime_dir"; fi' \
                --run 'cd "$runtime_dir"' \
                --run 'export PYTHONPATH="$runtime_dir''${PYTHONPATH:+:$PYTHONPATH}"' \
                --prefix PATH : ${runtimePath} \
                --add-flags frontends/tuiapp_v2.py

              makeWrapper ${pythonEnv}/bin/python $out/bin/genericagent-tui-v3 \
                --run 'runtime_dir="''${GENERICAGENT_HOME:-''${XDG_DATA_HOME:-$HOME/.local/share}/genericagent}"' \
                --run 'if [ ! -e "$runtime_dir/ga_cli" ]; then mkdir -p "$runtime_dir"; cp -R "'$out'/share/genericagent/." "$runtime_dir/"; chmod -R u+w "$runtime_dir"; fi' \
                --run 'cd "$runtime_dir"' \
                --run 'export PYTHONPATH="$runtime_dir''${PYTHONPATH:+:$PYTHONPATH}"' \
                --prefix PATH : ${runtimePath} \
                --add-flags frontends/tui_v3.py

              ln -s $out/bin/genericagent-tui $out/bin/ga-tui

              runHook postInstall
            '';

            meta = {
              description = "GenericAgent packaged with terminal UI dependencies";
              homepage = "https://github.com/lsdefine/GenericAgent";
              license = lib.licenses.mit;
              mainProgram = "genericagent-tui";
              platforms = systems;
            };
          };

          default = genericagent-tui;
        });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/genericagent-tui";
          meta.description = "Run the GenericAgent terminal UI";
        };
        ga = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/ga";
          meta.description = "Run the GenericAgent command dispatcher";
        };
      });

      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          pythonEnv = pythonEnvFor pkgs;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.git
            ];

            shellHook = ''
              export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
              export PATH="$PWD:$PATH"
              echo "GenericAgent dev shell: run 'ga list' or 'python frontends/tuiapp_v2.py'"
            '';
          };
        });
    };
}
