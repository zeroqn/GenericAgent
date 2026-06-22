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
      pythonFor = pkgs:
        pkgs.python312.override {
          packageOverrides = pySelf: pySuper: {
            # These overrides avoid current nixpkgs dev-shell failures/slow
            # builds while keeping GenericAgent runtime modules available.
            altair = pySuper.altair.overridePythonAttrs (old: {
              doCheck = false;
              # Check-only helper; otherwise this pulls a large Rust-backed
              # vl-convert-python build just to enter the dev shell.
              nativeBuildInputs = builtins.filter
                (dep: (dep.pname or dep.name or "") != "vl-convert-python")
                (old.nativeBuildInputs or []);
            });
            apscheduler = pySuper.apscheduler.overridePythonAttrs (_old: {
              doCheck = false;
            });
            streamlit = pySuper.streamlit.overridePythonAttrs (old: {
              # GenericAgent does not use Streamlit's pydeck chart integration;
              # omitting it avoids a large Jupyter/vl-convert Rust build when
              # entering the dev shell.
              dependencies = builtins.filter
                (dep: (dep.pname or dep.name or "") != "pydeck")
                (old.dependencies or []);
              propagatedBuildInputs = builtins.filter
                (dep: (dep.pname or dep.name or "") != "pydeck")
                (old.propagatedBuildInputs or []);
              pythonRemoveDeps = (old.pythonRemoveDeps or []) ++ [ "pydeck" ];
            });
          };
        };
      pythonEnvFor = pkgs:
        (pythonFor pkgs).withPackages (ps: with ps; [
          aiohttp
          beautifulsoup4
          bottle
          cryptography
          pillow
          prompt-toolkit
          psutil
          pycryptodome
          python-telegram-bot
          pywebview
          qrcode
          requests
          rich
          simple-websocket-server
          streamlit
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
          genericagent = pkgs.stdenvNoCC.mkDerivation {
            pname = "genericagent";
            version = "0.1.0";
            src = source;

            nativeBuildInputs = [ pkgs.makeWrapper pkgs.ast-grep ];

            dontConfigure = true;
            dontBuild = true;
            doCheck = true;

            checkPhase = ''
              runHook preCheck
              ${pythonEnv}/bin/python scripts/check_runtime_writes.py --ast-grep ${pkgs.ast-grep}/bin/ast-grep --self-test
              runHook postCheck
            '';

            installPhase = ''
              runHook preInstall

              mkdir -p $out/share/genericagent $out/bin $out/libexec
              cp -R . $out/share/genericagent
              chmod -R u+w $out/share/genericagent

              cat > $out/libexec/genericagent-init-runtime <<EOF
#!${pkgs.runtimeShell}
set -euo pipefail

export PATH="${pkgs.coreutils}/bin:\$PATH"

app_dir="$out/share/genericagent"
runtime_dir="\''${1:?runtime directory is required}"

if [ -e "\$runtime_dir" ] && [ ! -d "\$runtime_dir" ]; then
  echo "GENERICAGENT_HOME points to a non-directory: \$runtime_dir" >&2
  exit 1
fi

mkdir -p \
  "\$runtime_dir" \
  "\$runtime_dir/memory" \
  "\$runtime_dir/temp" \
  "\$runtime_dir/temp/model_responses" \
  "\$runtime_dir/bbs_files" \
  "\$runtime_dir/assets" \
  "\$runtime_dir/assets/tmwd_cdp_bridge"

# Chrome extensions load config.js from the extension directory.  Keep this
# directory as an explicit writable runtime overlay, but copy app-owned files
# as real files/directories instead of symlinking into the Nix store.
for sub in "\$app_dir/assets/tmwd_cdp_bridge"/* "\$app_dir/assets/tmwd_cdp_bridge"/.[!.]* "\$app_dir/assets/tmwd_cdp_bridge"/..?*; do
  [ -e "\$sub" ] || continue
  subname="\$(basename "\$sub")"
  [ "\$subname" = "config.js" ] && continue
  dest="\$runtime_dir/assets/tmwd_cdp_bridge/\$subname"
  rm -rf "\$dest"
  cp -R -L "\$sub" "\$dest"
done

if [ ! -e "\$runtime_dir/assets/tmwd_cdp_bridge/config.js" ]; then
  printf "const TID = '__ljq_%s';\n" "\$(date +%s)" > "\$runtime_dir/assets/tmwd_cdp_bridge/config.js"
fi
EOF
              chmod +x $out/libexec/genericagent-init-runtime

              makeWrapper ${pythonEnv}/bin/python $out/bin/ga \
                --run 'runtime_dir="''${GENERICAGENT_HOME:-/workspace/ga}"; export GENERICAGENT_HOME="$runtime_dir"; export GENERICAGENT_APP_ROOT="'$out'/share/genericagent"; "'$out'/libexec/genericagent-init-runtime" "$runtime_dir"; cd "$runtime_dir/temp"; export PYTHONPATH="'$out'/share/genericagent:$runtime_dir''${PYTHONPATH:+:$PYTHONPATH}"' \
                --prefix PATH : ${runtimePath} \
                --add-flags -m \
                --add-flags ga_cli

              runHook postInstall
            '';

            meta = {
              description = "GenericAgent command dispatcher packaged with runtime dependencies";
              homepage = "https://github.com/lsdefine/GenericAgent";
              license = lib.licenses.mit;
              mainProgram = "ga";
              platforms = systems;
            };
          };

          genericagent-tui = genericagent;
          default = genericagent;
        });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/ga";
          meta.description = "Run the GenericAgent command dispatcher";
        };
        ga = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/ga";
          meta.description = "Run the GenericAgent command dispatcher";
        };
      });

      checks = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          pythonEnv = pythonEnvFor pkgs;
        in
        {
          runtime-write-scan = pkgs.runCommand "genericagent-runtime-write-scan"
            { nativeBuildInputs = [ pythonEnv pkgs.ast-grep ]; }
            ''
              cp -R ${source} source
              chmod -R u+w source
              cd source
              python scripts/check_runtime_writes.py --ast-grep ast-grep --self-test
              touch $out
            '';
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
