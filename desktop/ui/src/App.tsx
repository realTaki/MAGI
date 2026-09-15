import { I18nProvider } from "./i18n";
import { ProductDemo } from "./ProductDemo";

export default function App() {
  return (
    <I18nProvider>
      <ProductDemo />
    </I18nProvider>
  );
}
