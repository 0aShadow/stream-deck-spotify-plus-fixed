import commonjs from "@rollup/plugin-commonjs";
import nodeResolve from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";
import typescript from "@rollup/plugin-typescript";
import path from "node:path";
import url from "node:url";
import fs from "node:fs";
import { execSync } from "child_process";

const isWatching = !!process.env.ROLLUP_WATCH;
const sdPlugin = "fr.dbenech.spotify-plus.sdPlugin";

/**
 * @type {import('rollup').RollupOptions}
 */
const config = {
	input: "src/plugin.ts",
	output: {
		file: `${sdPlugin}/bin/plugin.js`,
		sourcemap: isWatching,
		sourcemapPathTransform: (relativeSourcePath, sourcemapPath) => {
			return url.pathToFileURL(path.resolve(path.dirname(sourcemapPath), relativeSourcePath)).href;
		}
	},
	plugins: [
		{
			name: "watch-externals",
			buildStart: function () {
				this.addWatchFile(`${sdPlugin}/manifest.json`);
				fs.readdirSync('backend').forEach(file => {
					this.addWatchFile(`backend/${file}`);
				});
			},
		},
		{
			name: 'build-python-executable',
			buildStart() {
				const srcDir = 'backend';
				const buildDir = 'build'; // Build directory at project root

				// Determine platform
				const platformName = process.platform === 'win32' ? 'win32' :
					process.platform === 'darwin' ? 'mac' : 'linux';

				const executableName = process.platform === 'win32' ? 'streamdeck-spotify-plus-plugin.exe' : 'streamdeck-spotify-plus-plugin';

				// Final destination in plugin bin directory
				const finalDestDir = `${sdPlugin}/bin/backend`;
				const finalExecutablePath = path.join(finalDestDir, executableName);

				// Skip if executable already exists (for faster rebuilds)
				if (fs.existsSync(finalExecutablePath)) {
					console.log(`Executable already exists: ${finalExecutablePath}`);
					return;
				}

				// Clean up old backend directory structure if it exists
				const oldBackendDir = `${sdPlugin}/backend`;
				if (fs.existsSync(oldBackendDir)) {
					console.log('Cleaning up old backend directory structure...');
					fs.rmSync(oldBackendDir, { recursive: true, force: true });
				}

				// Skip building executable in watch mode for faster development
				if (isWatching) {
					console.log('Skipping executable build in watch mode for faster development');
					return;
				}

				try {
					console.log(`Building executable for ${platformName}...`);

					// Create build directory structure
					fs.mkdirSync(buildDir, { recursive: true });

					// Create final destination directory
					fs.mkdirSync(finalDestDir, { recursive: true });

					// Create virtual environment in build directory if it doesn't exist
					const venvDir = path.resolve(buildDir, 'venv');
					if (!fs.existsSync(venvDir)) {
						console.log('Creating Python virtual environment...');
						execSync(`python -m venv "${venvDir}"`);
					}

					// Python command path
					const pythonCmd = process.platform === 'win32'
						? path.resolve(venvDir, 'Scripts', 'python.exe')
						: path.resolve(venvDir, 'bin', 'python');

					// Install dependencies including PyInstaller
					console.log('Installing Python dependencies...');
					execSync(`"${pythonCmd}" -m pip install --upgrade pip --quiet`);
					execSync(`"${pythonCmd}" -m pip install --quiet -r "${path.resolve(srcDir, 'requirements.txt')}"`);
					execSync(`"${pythonCmd}" -m pip install --quiet "PyInstaller>=6.10.0"`);

					// Create PyInstaller spec file in build directory
					const specFile = path.join(buildDir, 'backend.spec');
					if (!fs.existsSync(specFile)) {
						console.log('Creating PyInstaller spec file...');
						const specContent = `# -*- mode: python ; coding: utf-8 -*-

import sys
import os
from PyInstaller.utils.hooks import collect_data_files

# Collect data files
datas = []
datas += collect_data_files('spotipy')
datas += collect_data_files('PIL')

# Add the PNG files that the backend needs
datas += [('${path.join('..', srcDir, 'spotify-like.png').replace(/\\/g, '/')}', '.'), ('${path.join('..', srcDir, 'spotify-liked.png').replace(/\\/g, '/')}', '.')]

a = Analysis(
    ['${path.join('..', srcDir, 'backend.py').replace(/\\/g, '/')}'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'spotipy',
        'spotipy.oauth2',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'requests',
        'flask',
        'dotenv',
        'single_dial',
        'font_utils'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='streamdeck-spotify-plus-plugin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)`;
						fs.writeFileSync(specFile, specContent);
					}

					// Clean previous build
					const distDir = path.join(buildDir, 'dist');
					const pyinstallerBuildDir = path.join(buildDir, 'build');
					if (fs.existsSync(distDir)) {
						fs.rmSync(distDir, { recursive: true });
					}
					if (fs.existsSync(pyinstallerBuildDir)) {
						fs.rmSync(pyinstallerBuildDir, { recursive: true });
					}

					// Build executable with PyInstaller
					console.log('Building executable with PyInstaller...');
					execSync(`"${pythonCmd}" -m PyInstaller --clean backend.spec`, {
						cwd: buildDir,
						stdio: 'inherit'
					});

					// Copy executable to final destination
					const builtExecutable = path.join(buildDir, 'dist', executableName);

					if (fs.existsSync(builtExecutable)) {
						// Copy to final bin destination
						fs.copyFileSync(builtExecutable, finalExecutablePath);

						// Make executable on Unix systems
						if (process.platform !== 'win32') {
							fs.chmodSync(finalExecutablePath, 0o755);
						}

						console.log(`Executable built and copied to: ${finalExecutablePath}`);
					} else {
						throw new Error(`Built executable not found: ${builtExecutable}`);
					}

				} catch (error) {
					console.error('Error building executable:', error);
					throw error;
				}
			}
		},
		typescript({
			mapRoot: isWatching ? "./" : undefined
		}),
		nodeResolve({
			browser: false,
			exportConditions: ["node"],
			preferBuiltins: true
		}),
		commonjs(),
		!isWatching && terser(),
		{
			name: "emit-module-package-file",
			generateBundle() {
				this.emitFile({ fileName: "package.json", source: `{ "type": "module" }`, type: "asset" });
			}
		}
	]
};

export default config;
