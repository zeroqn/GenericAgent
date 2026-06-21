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

            nativeBuildInputs = [ pkgs.makeWrapper ];

            dontConfigure = true;
            dontBuild = true;

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

mkdir -p "\$runtime_dir" "\$runtime_dir/memory" "\$runtime_dir/temp"

link_entry() {
  src="\$1"
  name="\$(basename "\$src")"

  case "\$name" in
    assets|memory|temp|mykey.py|mykey.json|boards.json|bbs_files|__pycache__)
      return
      ;;
  esac

  if [ ! -e "\$runtime_dir/\$name" ] && [ ! -L "\$runtime_dir/\$name" ]; then
    ln -s "\$src" "\$runtime_dir/\$name"
  fi
}

for src in "\$app_dir"/* "\$app_dir"/.[!.]* "\$app_dir"/..?*; do
  [ -e "\$src" ] || continue
  link_entry "\$src"
done

mkdir -p "\$runtime_dir/assets"
for src in "\$app_dir/assets"/* "\$app_dir/assets"/.[!.]* "\$app_dir/assets"/..?*; do
  [ -e "\$src" ] || continue
  name="\$(basename "\$src")"

  case "\$name" in
    tmwd_cdp_bridge)
      mkdir -p "\$runtime_dir/assets/tmwd_cdp_bridge"
      for sub in "\$app_dir/assets/tmwd_cdp_bridge"/* "\$app_dir/assets/tmwd_cdp_bridge"/.[!.]* "\$app_dir/assets/tmwd_cdp_bridge"/..?*; do
        [ -e "\$sub" ] || continue
        subname="\$(basename "\$sub")"
        [ "\$subname" = "config.js" ] && continue
        if [ ! -e "\$runtime_dir/assets/tmwd_cdp_bridge/\$subname" ] && [ ! -L "\$runtime_dir/assets/tmwd_cdp_bridge/\$subname" ]; then
          ln -s "\$sub" "\$runtime_dir/assets/tmwd_cdp_bridge/\$subname"
        fi
      done
      ;;
    *)
      if [ ! -e "\$runtime_dir/assets/\$name" ] && [ ! -L "\$runtime_dir/assets/\$name" ]; then
        ln -s "\$src" "\$runtime_dir/assets/\$name"
      fi
      ;;
  esac
done
EOF
              chmod +x $out/libexec/genericagent-init-runtime

              makeWrapper ${pythonEnv}/bin/python $out/bin/ga \
                --run 'runtime_dir="''${GENERICAGENT_HOME:-/workspace/ga}"' \
                --run '"'$out'/libexec/genericagent-init-runtime" "$runtime_dir"' \
                --run 'cd "$runtime_dir"' \
                --run 'export PYTHONPATH="$runtime_dir''${PYTHONPATH:+:$PYTHONPATH}"' \
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
