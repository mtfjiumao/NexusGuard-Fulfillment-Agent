import { requestJson } from './reception';
import type { ProductCard, UUID } from './types';

export function loadProduct(id: UUID) {
  return requestJson<ProductCard>(`/api/v1/presales/products/${encodeURIComponent(id)}`);
}

export function catalogImageUrl(path: string) {
  return path.replace('/assets/reception/', import.meta.env.BASE_URL);
}

export function productPrice(product: ProductCard) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency', currency: product.currency,
  }).format(product.price_cents / 100);
}
