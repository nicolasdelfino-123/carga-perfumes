from contextlib import contextmanager
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine,MetaData,Table,Column,Integer,String,JSON,LargeBinary
import transfers


class PerfumeFilter(unittest.TestCase):
    def test_mixed_catalog_only_lists_perfumes_and_rejects_direct_watch_ids(self):
        engine=create_engine('sqlite:///:memory:')
        meta=MetaData()
        category=Table('category',meta,Column('id',Integer,primary_key=True),Column('name',String))
        product=Table('product',meta,Column('id',Integer,primary_key=True),Column('name',String),Column('brand',String),
            Column('category_id',Integer),Column('flavor_catalog',JSON),Column('image_url',String),
            Column('description',String),Column('short_description',String))
        image=Table('product_images',meta,Column('id',Integer,primary_key=True),Column('product_id',Integer),
            Column('bytes',LargeBinary),Column('mime_type',String),Column('width',Integer),Column('height',Integer))
        meta.create_all(engine)
        categories=['Árabes Femeninos','Perfumes de Diseñador','Nicho Unisex','Relojes','Accesorios','Masculinos','Relojes de Diseñador']
        with engine.begin() as conn:
            conn.execute(category.insert(),[{'id':i,'name':name} for i,name in enumerate(categories,1)])
            for i in range(1,10):
                primary=i if i<=7 else 6 if i==8 else 4
                conn.execute(product.insert(),{'id':i,'name':f'Producto {i}','brand':'Marca','category_id':primary,
                    'flavor_catalog':[{'__type':'multi_category_meta','extra_category_ids':[1]}] if i>=8 else [],
                    'description':'Descripción','short_description':'Notas','image_url':None})
        @contextmanager
        def readonly_fixture(*args,**kwargs):
            with engine.connect() as conn:
                yield conn,{}
        tables={'product':product,'category':category,'product_images':image}
        with patch('transfers.connect',readonly_fixture),patch('transfers.schema',return_value=tables):
            self.assertEqual([row['id'] for row in transfers.source_products('juli')],[1,2,3,8])
            self.assertEqual([row['id'] for row in transfers.source_products('juli','Producto 2')],[2])
            for product_id in (4,5,6,7,9):
                with self.assertRaises(transfers.TransferError):
                    transfers.extract('juli',{product_id})
            self.assertEqual(len(transfers.extract('juli',{1,2,3,8})),4)
        engine.dispose()

    def test_category_names_with_accents_gender_and_unknowns(self):
        for name in ['ÁRABES','Árabes Masculinos','Árabes Unisex','Diseñador Fem','Nicho Árabes','Niche']:
            self.assertTrue(transfers.is_perfume_category(name),name)
        for name in ['Relojes','Accesorios','Masculinos','Femeninos','Unisex','Perfumes','Relojes diseñador']:
            self.assertFalse(transfers.is_perfume_category(name),name)


if __name__=='__main__':
    unittest.main()
