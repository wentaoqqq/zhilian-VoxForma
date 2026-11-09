"""Product configuration and product-local media assignments."""
from fastapi import APIRouter, HTTPException

from production import product_store
from production.product_models import AssetUpdate, Product

router = APIRouter()


def product_or_404(product_id: str) -> dict:
    try:
        return product_store.get_product(product_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/products')
def list_products():
    return product_store.list_products()


@router.post('/products')
def create_product(product: Product):
    # Creation always gets a new identity, even if the caller sends a copied ID.
    try:
        return product_store.save_product(product.model_copy(update={'id': None}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put('/products/{product_id}')
def update_product(product_id: str, product: Product):
    product_or_404(product_id)
    try:
        return product_store.save_product(product, product_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete('/products/{product_id}')
def delete_product(product_id: str):
    product_or_404(product_id)
    try:
        return product_store.delete_product(product_id)
    except product_store.ProductBusyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put('/products/{product_id}/assets/{asset_id}')
def update_asset(product_id: str, asset_id: str, assignment: AssetUpdate):
    try:
        product_store.get_asset(product_id, asset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        return product_store.update_asset(product_id, asset_id, **assignment.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
