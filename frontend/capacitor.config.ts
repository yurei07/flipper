import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.flipper.app',
  appName: 'flipper',
  webDir: 'dist',
  server: {
    androidScheme: 'http',
    cleartext: true,
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 900,
      backgroundColor: '#0b0f14',
      showSpinnerClassName: 'splash-spinner',
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#0b0f14',
      overlaysWebView: true,
    },
  },
};

export default config;